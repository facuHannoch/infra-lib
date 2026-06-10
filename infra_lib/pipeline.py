"""The deploy pipeline and management operations.

`deploy()` is provider-agnostic orchestration. It reports progress and asks for
interaction through the active Reporter (see progress.py): silent by default
(API / MCP), interactive when the CLI installs a console reporter.
"""
from typing import Optional

from . import progress
from .models import Infrastructure, Deployment, Service
from .core import registry
from .core.keys import ensure_key, key_path
from .core.domain import default_caddyfile
from .core.transfer import transfer, run_setup, start_service, ssh_exec
from .core.health import wait_for_url, wait_for_port
from .providers import get_provider


def _provider_for(name: str):
    """The provider that owns deployment `name`, per the deployment registry.

    Falls back to the default provider for deployments created before the
    registry existed (legacy Azure stacks).
    """
    return get_provider(registry.provider_of(name, default="azure"))


def deploy(infra: Infrastructure, ssh_key_path: str = None) -> Deployment:
    """Provision `infra` and return the live Deployment.

    Picks the path from two axes: the workload (process vs container, inferred
    from the machine) and the provider's `kind` (vm vs container_host). Operates
    on a single machine for now (infra.machines[0]). Silent by default — progress
    flows through the active Reporter (see progress.py).
    """
    if not infra.machines:
        raise ValueError("Infrastructure has no machines to deploy.")
    if len(infra.machines) > 1:
        raise NotImplementedError(
            "Multi-machine deployments aren't implemented yet (see todo.md). "
            "Pass a single machine for now."
        )
    provider = get_provider(infra.provider)
    machine = infra.machines[0]
    workload = "container" if machine.is_container else "process"
    if workload not in provider.workloads:
        raise ValueError(
            f"The {provider.name} provider can't run a '{workload}' workload "
            f"(it supports: {', '.join(sorted(provider.workloads))}). "
            + ("Give it an image/build to run a container."
               if workload == "process"
               else "Drop image/build to deploy files+process, or pick a container host.")
        )

    if workload == "container":
        return _deploy_container(infra, provider, machine)
    return _deploy_process(infra, provider, machine, ssh_key_path)


def _deploy_process(infra, provider, machine, ssh_key_path=None) -> Deployment:
    """The VM path: provision a box, ship files, supervise via systemd, expose."""
    r = progress.reporter()
    user = provider.admin_user
    name = infra.name
    domain = machine.domain
    port = machine.ports[0] if machine.ports else None
    ssh_key_path = ssh_key_path or ensure_key(name)

    # Resolve the sizing request (ExpectedSpecs or exact VMSpec) into a concrete,
    # available VMSpec, and store it back so the deployment records what it got.
    machine.hardware = provider.resolve(machine.hardware, infra.location)

    outputs = provider.provision(
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
    transfer(ip, ship=machine.ship, caddyfile=caddyfile, ssh_key_path=ssh_key_path, user=user)

    if machine.setup:
        run_setup(ip, machine.setup, ssh_key_path=ssh_key_path, user=user)

    if machine.start:
        start_service(ip, name, machine.start, ssh_key_path=ssh_key_path, user=user)

    public_url = domain.url() if domain else (f"http://{ip}" if has_content else None)

    if public_url:
        if port and not wait_for_port(ip, port, ssh_key_path, user=user):
            r.warn(f"App is not listening on port {port} after 60s.")
            r.warn("Check your setup started the app, or SSH in and inspect logs.")
        if r.confirm_test():
            wait_for_url(public_url)

    services = [Service(port=port or 80, url=public_url)] if public_url else []
    result = Deployment(name=name, ip=ip, ssh_key=ssh_key_path, user=user, services=services)
    registry.record(name, provider.name, handle=name, url=public_url or "", ssh_key=ssh_key_path)
    r.finished(result)
    return result


def _deploy_container(infra, provider, machine) -> Deployment:
    """The container path: package an image and run it.

    On a container host (RunPod) the provider's launch() does provision+run+
    expose in one call. (Container-on-VM isn't wired yet — see todo.md.)
    """
    r = progress.reporter()
    name = infra.name
    machine.hardware = provider.resolve(machine.hardware, infra.location)

    if machine.build:
        from .core.container import build_and_push
        image = build_and_push(machine.build, machine.registry, name)
    else:
        image = machine.image

    if provider.kind != "container_host":
        raise NotImplementedError(
            "Running a container on a VM provider (Docker-on-VM) isn't wired yet — "
            "use a container host like 'runpod'. See todo.md."
        )

    out = provider.launch(
        name=name, vm_spec=machine.hardware, image=image,
        ports=machine.ports, env=machine.env, command=machine.start,
        storage_gb=machine.disk.size_gb,
    )
    url, handle = out.get("url"), out.get("handle", name)
    port = machine.ports[0] if machine.ports else 80

    registry.record(name, provider.name, handle=handle, url=url or "", ssh_key="")
    services = [Service(port=port, url=url)] if url else []
    result = Deployment(name=name, ip=handle, ssh_key="", user=provider.admin_user, services=services)
    if url and r.confirm_test():
        wait_for_url(url)
    r.finished(result)
    return result


def _to_deployment(d: dict, provider_name: str = "azure") -> Deployment:
    ip = d.get("ip", "")
    url = d.get("url", "")
    services = [Service(port=80, url=url)] if url and url != "-" else []
    return Deployment(
        name=d["name"],
        ip=ip if ip != "-" else "",
        ssh_key=d.get("ssh_key", key_path(d["name"])),
        user=get_provider(provider_name).admin_user,
        services=services,
    )


def get(name: str) -> Optional[Deployment]:
    pname = registry.provider_of(name, default="azure")
    for d in get_provider(pname).list_deployments():
        if d["name"] == name:
            return _to_deployment(d, pname)
    return None


def list_deployments() -> list[Deployment]:
    # Query every provider that has a recorded deployment (plus Azure, where
    # legacy stacks live), and merge by name. A provider that isn't authed yet
    # is skipped rather than failing the whole listing.
    providers = {e["provider"] for e in registry.all()} | {"azure"}
    seen: dict[str, Deployment] = {}
    for pname in providers:
        try:
            for d in get_provider(pname).list_deployments():
                seen[d["name"]] = _to_deployment(d, pname)
        except Exception:
            continue
    return list(seen.values())


def run(name: str, command: str) -> str:
    d = get(name)
    if not d:
        raise ValueError(f"Deployment '{name}' not found")
    if not d.ssh_key:
        raise ValueError(f"'{name}' is a container deployment — SSH (run/logs/connect) isn't available.")
    out, err, _ = ssh_exec(d.ip, command, d.ssh_key, user=d.user)
    return out + err if err else out


def connect(name: str) -> str:
    """Return a ready-to-run SSH command for the deployment.

    The library already knows the IP and key, so callers (CLI, agents) don't
    have to look them up. The CLI uses this to drop into an interactive shell.
    """
    d = get(name)
    if not d:
        raise ValueError(f"Deployment '{name}' not found")
    if not d.ssh_key:
        raise ValueError(f"'{name}' is a container deployment — SSH isn't available.")
    return d.ssh_command


def logs(name: str, lines: int = 50) -> str:
    """Return the most recent journal logs for the deployment's service."""
    return run(name, f"sudo journalctl -u {name} -n {lines} --no-pager")


def destroy(name: str, purge: bool = True) -> None:
    """Tear down a deployment (lower-level; `down` is the API verb)."""
    _provider_for(name).destroy(name, purge=purge)
    if purge:
        registry.remove(name)


def down(name: str) -> None:
    destroy(name, purge=True)


def pause(name: str) -> None:
    """Pause the deployment — stops compute billing, keeps the disk/volume."""
    _provider_for(name).pause(name)


def resume(name: str) -> None:
    """Start a paused deployment back up."""
    _provider_for(name).resume(name)
