import os
import yaml
from typing import Optional

from .models import Infrastructure, Unit, ShipItem, Disk, ExpectedSpecs, VMSpec
from .core.domain import build_domain
from .providers import get_provider

CONFIG_FILENAME = "infra.yml"

# The provider a unit `type` lands on when `provider:` isn't given explicitly.
_DEFAULT_PROVIDER_FOR_TYPE = {"vm": "azure", "pod": "runpod"}

# Keys that configure a single unit. When they appear at the top level, the file
# is the flat (one-unit) form; under `units:`/`machines:` they're per-unit.
_UNIT_KEYS = {
    "type", "size", "cpu", "ram", "gpu", "instance_type", "storage", "disk_type",
    "ship", "setup", "start", "port", "ports", "env",
    "domain", "domain_strategy", "proxied", "cloudflare_token",
    "image", "build", "registry",
}


def parse_gpu(val) -> tuple[int, Optional[str]]:
    """Normalize a `gpu:` value into (count, type). Accepts several shapes::

        gpu: a100            -> (1, "a100")     # by type name
        gpu: 2               -> (2, None)       # by count, any type
        gpu: true            -> (1, None)       # just "give me a GPU"
        gpu: {type: t4, count: 4}  -> (4, "t4")

    Returns (0, None) when no GPU is requested.
    """
    if val is None or val is False:
        return 0, None
    if val is True:
        return 1, None
    if isinstance(val, int):
        return val, None
    if isinstance(val, str):
        return 1, val.lower()
    if isinstance(val, dict):
        count = int(val.get("count", 1))
        gtype = str(val["type"]).lower() if val.get("type") else None
        return count, gtype
    raise ValueError("'gpu' must be a count, a type name, or a {type, count} mapping.")


def default_provider_for_type(unit_type: str) -> str:
    return _DEFAULT_PROVIDER_FOR_TYPE.get(unit_type, "azure")


def load_config(path: str = None) -> Optional[Infrastructure]:
    """Read infra.yml into an Infrastructure. Returns None when no file is present.

    Two shapes are accepted:

    Flat (one unit — the common case)::

        name: at1
        type: vm
        cpu: 4
        ship: [.]
        domain: at1.example.com

    Nested (one or more units keyed by name)::

        name: at1
        units:
          web:
            cpu: 4
            ports: [3000]
            domain: at1.example.com

    The provider is derived from the unit's `type` (vm -> azure, pod -> runpod)
    unless given explicitly as `provider:`.
    """
    path = path or os.path.join(os.getcwd(), CONFIG_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    base = os.path.dirname(path)
    nested = data.get("units") or data.get("machines")

    # The provider is an infra-level choice; default it from the (first) unit type.
    first_type = _first_type(data, nested)
    provider = str(data.get("provider") or default_provider_for_type(first_type))

    if nested is not None:
        units = _parse_units(nested, base, provider)
    else:
        units = [_parse_unit(data, base, provider)]

    return Infrastructure(
        name=str(data.get("name", "default")),
        location=str(data.get("location", "CentralUS")),
        provider=provider,
        units=units,
    )


def _first_type(data: dict, nested) -> str:
    if nested is None:
        return str(data.get("type", "vm"))
    if isinstance(nested, dict):
        first = next(iter(nested.values()), {}) or {}
    elif isinstance(nested, list):
        first = (nested[0] if nested else {}) or {}
    else:
        return "vm"
    return str(first.get("type", "vm"))


def _parse_units(nested, base: str, provider: str) -> list[Unit]:
    """Parse the `units:`/`machines:` section (a name->config mapping, or a list)."""
    if isinstance(nested, dict):
        return [_parse_unit(cfg or {}, base, provider, name=name)
                for name, cfg in nested.items()]
    if isinstance(nested, list):
        return [_parse_unit(cfg or {}, base, provider, name=(cfg or {}).get("name"))
                for cfg in nested]
    raise ValueError("'units' must be a mapping of name -> config (or a list).")


def _parse_ship(raw, base: str) -> list[ShipItem]:
    """Parse `ship` entries, each `SRC` or `SRC:DEST` (split on the first ':').

    A source path containing a ':' is unsupported (rare for real paths). DEST is
    kept verbatim (absolute or ~-relative); SRC is resolved against the config dir.
    """
    if isinstance(raw, str):
        raw = [raw]
    items = []
    for entry in raw or []:
        src, sep, dest = str(entry).partition(":")
        abs_src = os.path.abspath(os.path.join(base, src))
        items.append(ShipItem(src=abs_src, dest=dest.strip() if sep else None))
    return items


def _parse_unit(data: dict, base: str, provider: str, name: str = None) -> Unit:
    unit_type = str(data.get("type", "vm")).lower()

    ship = _parse_ship(data.get("ship", []), base)

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

    # Sizing: instance_type (exact) > cpu/ram (raw) > gpu/size (preset) > default small.
    # GPU is orthogonal: it layers onto an ExpectedSpecs (and a GPU box doesn't
    # force a CPU preset, since the SKU bundles its own cpu/ram).
    gpu_count, gpu_type = parse_gpu(data.get("gpu"))
    if data.get("instance_type"):
        hardware = VMSpec(type=str(data["instance_type"]))
    elif data.get("cpu") or data.get("ram"):
        hardware = ExpectedSpecs(cpu=int(data.get("cpu", 2)), ram_gb=float(data.get("ram", 8)))
    elif gpu_count or gpu_type:
        hardware = ExpectedSpecs()
    else:
        hardware = get_provider(provider).preset_specs(str(data.get("size", "small")))
    if (gpu_count or gpu_type) and isinstance(hardware, ExpectedSpecs):
        hardware.gpu = gpu_count or 1
        hardware.gpu_type = gpu_type

    build = data.get("build")
    if build is not None:
        build = os.path.abspath(os.path.join(base, str(build)))

    image = str(data["image"]) if data.get("image") else None

    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("'env' must be a mapping of NAME: value.")
    env = {str(k): str(v) for k, v in env.items()}

    if unit_type == "pod" and not (image or build):
        raise ValueError(
            "A 'pod' unit boots from an image — set `image:` (or `build:` a Dockerfile). "
            "Use `type: vm` to ship files + run commands instead."
        )

    raw_storage = data.get("storage")
    return Unit(
        name=name,
        type=unit_type,
        hardware=hardware,
        disk=Disk(size_gb=int(raw_storage) if raw_storage else 30,
                  type=str(data.get("disk_type", "standard"))),
        ship=ship,
        setup=list(setup),
        ports=ports,
        start=start,
        domain=domain,
        env=env,
        image=image,
        build=build,
        registry=str(data["registry"]) if data.get("registry") else None,
    )
