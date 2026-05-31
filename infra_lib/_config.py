import os
import yaml
from dataclasses import dataclass, field
from typing import Optional
from ._spec import VMSpec
from ._resolve import AZURE_PRESETS

CONFIG_FILENAME = "infra.yml"


@dataclass
class InfraConfig:
    name: str = "default"
    vm: str = "small"
    location: str = "CentralUS"
    domain: Optional[str] = None
    domain_strategy: Optional[str] = None
    proxied: bool = False
    port: Optional[int] = None
    ship: list[str] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)

    def vm_spec(self) -> VMSpec:
        preset = AZURE_PRESETS.get(self.vm)
        if not preset:
            raise ValueError(f"Unknown vm preset '{self.vm}'. Choose from: {', '.join(AZURE_PRESETS)}")
        return VMSpec(cpu=preset["cpu"], ram_gb=preset["ram_gb"])


def load_config(path: str = None) -> Optional[InfraConfig]:
    path = path or os.path.join(os.getcwd(), CONFIG_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    ship = data.get("ship", [])
    if isinstance(ship, str):
        ship = [ship]

    setup = data.get("setup", [])
    if isinstance(setup, str):
        setup = [setup]

    raw_port = data.get("port")
    return InfraConfig(
        name=str(data.get("name", "default")),
        vm=str(data.get("vm", "small")),
        location=str(data.get("location", "CentralUS")),
        domain=data.get("domain"),
        domain_strategy=data.get("domain_strategy"),
        proxied=bool(data.get("proxied", False)),
        port=int(raw_port) if raw_port else None,
        ship=[os.path.abspath(os.path.join(os.path.dirname(path), p)) for p in ship],
        setup=setup,
    )
