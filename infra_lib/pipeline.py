"""The deploy pipeline and management operations.

`deploy()` is provider-agnostic orchestration: it runs the same ordered steps for
every unit — create -> ship -> setup -> start -> expose -> health — and a step
no-ops when the substrate can't do it (a pod with no SSH skips ship/setup). The
provider owns the steps that differ by substrate (create/start/expose); ship and
setup are shared SSH code run here when the unit's Endpoint exposes SSH.

It reports progress and asks for interaction through the active Reporter (see
progress.py): silent by default (API / MCP), interactive under the CLI.
"""
from typing import Optional

from . import progress
from .models import Infrastructure, Deployment, Service
from .core import registry
from .core.keys import ensure_key, key_path
from .core.transfer import transfer, run_setup, ssh_exec
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

    Operates on a single unit for now (infra.units[0]). Silent by default —
    progress flows through the active Reporter (see progress.py).
    """
    if not infra.units:
        raise ValueError("Infrastructure has no units to deploy.")
    if len(infra.units) > 1:
        raise NotImplementedError(
            "Multi-unit deployments aren't implemented yet (see todo.md). "
            "Pass a single unit for now."
        )
    provider = get_provider(infra.provider)
    unit = infra.units[0]
    if unit.type != provider.unit_type:
        raise ValueError(
            f"The {provider.name} provider realizes '{provider.unit_type}' units, "
            f"not '{unit.type}'. Use a matching provider or change the unit's type."
        )

    r = progress.reporter()
    name = infra.name
    ssh_key_path = ssh_key_path or ensure_key(name)

    # Resolve the sizing request (ExpectedSpecs or exact VMSpec) into a concrete,
    # available VMSpec, and store it back so the deployment records what it got.
    unit.hardware = provider.resolve(unit.hardware, infra.location)

    # Package: a `build` dir is turned into an image ref before create() runs.
    if unit.build:
        from .core.container import build_and_push
        unit.image = build_and_push(unit.build, unit.registry, name)

    # 1. create the substrate (and, for a pod, the running container).
    endpoint = provider.create(name, infra.location, ssh_key_path, unit)

    # 2-3. ship + setup — only where the substrate gives us SSH.
    if endpoint.has_ssh:
        transfer(endpoint.host, ship=unit.ship, ssh_key_path=endpoint.ssh_key,
                 user=endpoint.user, port=endpoint.ssh_port, home=endpoint.home,
                 sudo=endpoint.sudo)
        if unit.setup:
            run_setup(endpoint.host, unit.setup, ssh_key_path=endpoint.ssh_key,
                      user=endpoint.user, port=endpoint.ssh_port)
    elif unit.ship or unit.setup:
        r.warn("Skipped ship/setup: this unit has no SSH access.")

    # 4. start the long-running command (vm: systemd; pod: already running).
    provider.start(endpoint, name, unit)

    # 5. expose (vm: Caddy + DNS; pod: the proxy URL it already has).
    url = provider.expose(endpoint, name, unit)

    # 6. health.
    port = unit.ports[0] if unit.ports else None
    if url:
        if port and endpoint.has_ssh and endpoint.ssh_port == 22:
            if not wait_for_port(endpoint.host, port, endpoint.ssh_key, user=endpoint.user):
                r.warn(f"App is not listening on port {port} after 60s.")
                r.warn("Check your setup started the app, or SSH in and inspect logs.")
        if r.confirm_test():
            wait_for_url(url)

    services = [Service(port=port or 80, url=url)] if url else []
    ssh_key = endpoint.ssh_key if endpoint.has_ssh else ""
    result = Deployment(name=name, ip=endpoint.host, ssh_key=ssh_key,
                        user=endpoint.user, services=services)
    registry.record(name, provider.name, handle=endpoint.handle or name,
                    url=url or "", ssh_key=ssh_key)
    r.finished(result)
    return result


def _to_deployment(d: dict, provider_name: str = "azure") -> Deployment:
    ip = d.get("ip", "")
    url = d.get("url", "")
    services = [Service(port=80, url=url)] if url and url != "-" else []
    ssh_key = d.get("ssh_key", key_path(d["name"]))
    if ssh_key == "-":
        ssh_key = ""
    return Deployment(
        name=d["name"],
        ip=ip if ip != "-" else "",
        ssh_key=ssh_key,
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
        raise ValueError(f"'{name}' has no SSH access — run/logs/connect aren't available.")
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
        raise ValueError(f"'{name}' has no SSH access — connect isn't available.")
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
