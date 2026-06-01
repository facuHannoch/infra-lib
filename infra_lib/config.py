import os
import yaml
from typing import Optional

from .models import Infrastructure, Machine, Disk, ExpectedSpecs, VMSpec
from .core.domain import build_domain
from .providers.azure.sizes import expectedspecs_from_preset

CONFIG_FILENAME = "infra.yml"


def load_config(path: str = None) -> Optional[Infrastructure]:
    """Read infra.yml into an Infrastructure (single machine).

    The file stays flat for the common one-machine case; it maps onto
    machines[0]. Returns None when no config file is present.
    """
    path = path or os.path.join(os.getcwd(), CONFIG_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    base = os.path.dirname(path)

    ship = data.get("ship", [])
    if isinstance(ship, str):
        ship = [ship]
    ship = [os.path.abspath(os.path.join(base, p)) for p in ship]

    setup = data.get("setup", [])
    if isinstance(setup, str):
        setup = [setup]

    raw_port = data.get("port")
    raw_storage = data.get("storage")

    start = data.get("start")
    if isinstance(start, (list, dict)):
        raise ValueError("'start' must be a single command string (a service has one ExecStart).")

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
        hardware = expectedspecs_from_preset(str(data.get("vm", "small")))

    machine = Machine(
        hardware=hardware,
        disk=Disk(size_gb=int(raw_storage) if raw_storage else 30),
        ship=ship,
        setup=list(setup),
        ports=[int(raw_port)] if raw_port else [],
        start=start,
        domain=domain,
    )

    return Infrastructure(
        name=str(data.get("name", "default")),
        location=str(data.get("location", "CentralUS")),
        machines=[machine],
    )
