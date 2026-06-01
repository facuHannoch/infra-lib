import os
import yaml
from typing import Optional

from .models import Infrastructure, Machine, Disk, ExpectedSpecs, VMSpec
from .core.domain import build_domain
from .providers import get_provider

CONFIG_FILENAME = "infra.yml"

# Keys that configure a single machine. When they appear at the top level, the
# file is the flat (one-machine) form; under `machines:` they're per-machine.
_MACHINE_KEYS = {
    "vm", "cpu", "ram", "instance_type", "storage", "disk_type",
    "ship", "setup", "start", "port", "ports",
    "domain", "domain_strategy", "proxied", "cloudflare_token",
}


def load_config(path: str = None) -> Optional[Infrastructure]:
    """Read infra.yml into an Infrastructure. Returns None when no file is present.

    Two shapes are accepted:

    Flat (one machine — the common case)::

        name: at1
        vm: small
        ship: [.]
        domain: at1.example.com

    Nested (one or more machines keyed by name)::

        name: at1
        machines:
          web:
            vm: small
            ports: [3000]
            domain: at1.example.com
    """
    path = path or os.path.join(os.getcwd(), CONFIG_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    base = os.path.dirname(path)
    provider = str(data.get("provider", "azure"))

    nested = data.get("machines")
    if nested is not None:
        machines = _parse_machines(nested, base, provider)
    else:
        machines = [_parse_machine(data, base, provider)]

    return Infrastructure(
        name=str(data.get("name", "default")),
        location=str(data.get("location", "CentralUS")),
        provider=provider,
        machines=machines,
    )


def _parse_machines(nested, base: str, provider: str) -> list[Machine]:
    """Parse the `machines:` section (a name->config mapping, or a list)."""
    if isinstance(nested, dict):
        return [_parse_machine(cfg or {}, base, provider, name=name)
                for name, cfg in nested.items()]
    if isinstance(nested, list):
        return [_parse_machine(cfg or {}, base, provider, name=(cfg or {}).get("name"))
                for cfg in nested]
    raise ValueError("'machines' must be a mapping of name -> config (or a list).")


def _parse_machine(data: dict, base: str, provider: str, name: str = None) -> Machine:
    ship = data.get("ship", [])
    if isinstance(ship, str):
        ship = [ship]
    ship = [os.path.abspath(os.path.join(base, p)) for p in ship]

    setup = data.get("setup", [])
    if isinstance(setup, str):
        setup = [setup]

    start = data.get("start")
    if isinstance(start, (list, dict)):
        raise ValueError("'start' must be a single command string (a service has one ExecStart).")

    # Ports: `ports` (list/int) takes precedence; `port` is the single-port alias.
    ports = data.get("ports")
    if ports is None:
        raw_port = data.get("port")
        ports = [int(raw_port)] if raw_port else []
    elif isinstance(ports, int):
        ports = [ports]
    else:
        ports = [int(p) for p in ports]

    domain = build_domain(
        name=data.get("domain"),
        strategy=data.get("domain_strategy"),
        proxied=bool(data.get("proxied", False)),
        cloudflare_token=data.get("cloudflare_token") or os.environ.get("CLOUDFLARE_API_TOKEN"),
    )

    # Sizing: instance_type (exact) > cpu/ram (raw) > vm (preset) > default small.
    if data.get("instance_type"):
        hardware = VMSpec(type=str(data["instance_type"]))
    elif data.get("cpu") or data.get("ram"):
        hardware = ExpectedSpecs(cpu=int(data.get("cpu", 2)), ram_gb=float(data.get("ram", 8)))
    else:
        hardware = get_provider(provider).preset_specs(str(data.get("vm", "small")))

    raw_storage = data.get("storage")
    return Machine(
        name=name,
        hardware=hardware,
        disk=Disk(size_gb=int(raw_storage) if raw_storage else 30,
                  type=str(data.get("disk_type", "standard"))),
        ship=ship,
        setup=list(setup),
        ports=ports,
        start=start,
        domain=domain,
    )
