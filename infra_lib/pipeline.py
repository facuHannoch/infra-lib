"""The deploy pipeline and management operations.

`deploy()` is provider-agnostic orchestration. It reports progress and asks for
interaction through the active Reporter (see progress.py): silent by default
(API / MCP), interactive when the CLI installs a console reporter.
"""
from typing import Optional

from . import progress
from .models import Infrastructure, Deployment, Service
from .core.keys import ensure_key, key_path
from .core.domain import default_caddyfile
from .core.transfer import transfer, run_setup, start_service, ssh_exec
from .core.health import wait_for_url, wait_for_port
from .providers.azure.provision import provision, list_deployments as _raw_list, destroy
from .providers.azure.sizes import resolve as resolve_size


def deploy(infra: Infrastructure, ssh_key_path: str = None) -> Deployment:
    """Provision `infra` and return the live Deployment.

    Operates on a single machine for now (infra.machines[0]); the model leaves
    room for more. Silent by default — progress flows through the active
    Reporter (see progress.py).
    """
    r = progress.reporter()
    if not infra.machines:
        raise ValueError("Infrastructure has no machines to deploy.")
    if len(infra.machines) > 1:
        raise NotImplementedError(
            "Multi-machine deployments aren't implemented yet (see todo.md). "
            "Pass a single machine for now."
        )
    name = infra.name
    machine = infra.machines[0]
    domain = machine.domain
    port = machine.ports[0] if machine.ports else None
    ssh_key_path = ssh_key_path or ensure_key(name)

    # Resolve the sizing request (ExpectedSpecs or exact VMSpec) into a concrete,
    # available VMSpec, and store it back so the deployment records what it got.
    machine.hardware = resolve_size(machine.hardware, infra.location)

    outputs = provision(
        name=name,
        location=infra.location,
        ssh_key_path=ssh_key_path,
        vm_spec=machine.hardware,
        storage_gb=machine.disk.size_gb,
    )
    ip = outputs["public_ip"]
    r.show_ip(ip)

    if domain:
        if domain.auto_dns:
            r.step(f"Provisioning DNS for {domain.name}")
            domain.provision_dns(ip)
            r.done("DNS configured")
        else:
            r.need_dns(domain, ip)

    has_content = bool(machine.ship or port)
    caddyfile = domain.caddyfile(port=port) if domain else (default_caddyfile(port=port) if has_content else None)
    transfer(ip, ship=machine.ship, caddyfile=caddyfile, ssh_key_path=ssh_key_path)

    if machine.setup:
        run_setup(ip, machine.setup, ssh_key_path=ssh_key_path)

    if machine.start:
        start_service(ip, name, machine.start, ssh_key_path=ssh_key_path)

    public_url = domain.url() if domain else (f"http://{ip}" if has_content else None)

    if public_url:
        if port and not wait_for_port(ip, port, ssh_key_path):
            r.warn(f"App is not listening on port {port} after 60s.")
            r.warn("Check your setup started the app, or SSH in and inspect logs.")
        if r.confirm_test():
            wait_for_url(public_url)

    services = [Service(port=port or 80, url=public_url)] if public_url else []
    result = Deployment(name=name, ip=ip, ssh_key=ssh_key_path, services=services)
    r.finished(result)
    return result


def _to_deployment(d: dict) -> Deployment:
    ip = d.get("ip", "")
    url = d.get("url", "")
    services = [Service(port=80, url=url)] if url and url != "-" else []
    return Deployment(
        name=d["name"],
        ip=ip if ip != "-" else "",
        ssh_key=d.get("ssh_key", key_path(d["name"])),
        services=services,
    )


def get(name: str) -> Optional[Deployment]:
    for d in _raw_list():
        if d["name"] == name:
            return _to_deployment(d)
    return None


def list_deployments() -> list[Deployment]:
    return [_to_deployment(d) for d in _raw_list()]


def run(name: str, command: str) -> str:
    d = get(name)
    if not d:
        raise ValueError(f"Deployment '{name}' not found")
    out, err, _ = ssh_exec(d.ip, command, d.ssh_key)
    return out + err if err else out


def connect(name: str) -> str:
    """Return a ready-to-run SSH command for the deployment.

    The library already knows the IP and key, so callers (CLI, agents) don't
    have to look them up. The CLI uses this to drop into an interactive shell.
    """
    d = get(name)
    if not d:
        raise ValueError(f"Deployment '{name}' not found")
    return d.ssh_command


def logs(name: str, lines: int = 50) -> str:
    """Return the most recent journal logs for the deployment's service."""
    return run(name, f"sudo journalctl -u {name} -n {lines} --no-pager")


def down(name: str) -> None:
    destroy(name, purge=True)
