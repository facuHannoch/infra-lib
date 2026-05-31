"""The deploy pipeline and management operations.

`deploy()` is provider-agnostic orchestration. It reports progress and asks for
interaction through the active Reporter (see progress.py): silent by default
(API / MCP), interactive when the CLI installs a console reporter.
"""
from typing import Optional

from . import progress
from .models import Deployment, Service, VMSpec
from .core.keys import ensure_key, key_path
from .core.domain import Domain, default_caddyfile
from .core.transfer import transfer, run_setup, _connect, _wait_for_ssh
from .core.health import wait_for_url, wait_for_port
from .providers.azure.provision import provision, list_deployments as _raw_list, destroy
from .providers.azure.sizes import AZURE_PRESETS


def _vm_spec(vm: str) -> VMSpec:
    preset = AZURE_PRESETS.get(vm)
    if not preset:
        raise ValueError(f"Unknown vm preset '{vm}'. Choose from: {', '.join(AZURE_PRESETS)}")
    return VMSpec(cpu=preset["cpu"], ram_gb=preset["ram_gb"])


def deploy(
    name: str = "default",
    vm: str = "small",
    port: int = None,
    ship: list[str] = None,
    setup: list[str] = None,
    domain: Optional[Domain] = None,
    location: str = "CentralUS",
    ssh_key_path: str = None,
    source: str = None,
) -> Deployment:
    r = progress.reporter()
    spec = _vm_spec(vm)
    ssh_key_path = ssh_key_path or ensure_key(name)

    outputs = provision(name=name, location=location, ssh_key_path=ssh_key_path, vm_spec=spec)
    ip = outputs["public_ip"]
    r.show_ip(ip)

    if domain:
        if domain.auto_dns:
            r.step(f"Provisioning DNS for {domain.name}")
            domain.provision_dns(ip)
            r.done("DNS configured")
        else:
            r.need_dns(domain, ip)

    has_content = bool(source or ship or port)
    caddyfile = domain.caddyfile(port=port) if domain else (default_caddyfile(port=port) if has_content else None)
    transfer(ip, source_dir=source, ship=ship, caddyfile=caddyfile, ssh_key_path=ssh_key_path)

    if setup:
        run_setup(ip, setup, ssh_key_path=ssh_key_path)

    public_url = domain.url() if domain else (f"http://{ip}" if has_content else None)

    if public_url:
        if port and not wait_for_port(ip, port, ssh_key_path):
            r.warn(f"App is not listening on port {port} after 60s.")
            r.warn("Check your setup started the app, or SSH in and inspect logs.")
        if r.confirm_test():
            wait_for_url(public_url)

    services = [Service(port=port or 80, url=public_url)] if public_url else []
    result = Deployment(
        name=name,
        ip=ip,
        ssh_key=ssh_key_path,
        services=services,
        status="running" if services else "provisioned",
    )
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
        status="unknown",
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
    _wait_for_ssh(d.ip, d.ssh_key)
    client = _connect(d.ip, d.ssh_key)
    _, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode()
    err = stderr.read().decode()
    client.close()
    if err:
        output += err
    return output


def down(name: str) -> None:
    destroy(name, purge=True)
