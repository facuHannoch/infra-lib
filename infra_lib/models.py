from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .core.domain import Domain


# --- Desired state (input) ---------------------------------------------------
# What you want. Maps 1:1 to infra.yml. Passed to deploy() -> Deployment.

@dataclass
class ExpectedSpecs:
    """What you ask for: minimum cpu/ram. Transient, provider-agnostic.

    Resolved into a concrete VMSpec by the provider (see resolve()). Never stored
    on a Machine — it's only an input that produces a VMSpec.
    """
    cpu: int = 2
    ram_gb: float = 8


@dataclass
class VMSpec:
    """A concrete machine size: the provider's instance identifier plus its specs.

    This is what a Machine stores. Produced by resolving an ExpectedSpecs (cheapest
    that satisfies it) or by naming an exact `type` directly. `type` is the provider
    instance id (Azure SKU, e.g. 'Standard_D2s_v3'); cpu/ram/price are filled in by
    resolution and used for display.
    """
    type: str
    cpu: int = 0
    ram_gb: float = 0
    price_per_hour: Optional[float] = None

    def __str__(self):
        price = f"~${self.price_per_hour:.4f}/hr" if self.price_per_hour else "price unknown"
        return f"{self.type} ({self.cpu} vCPU, {self.ram_gb}GB RAM, {price})"


@dataclass
class Disk:
    """A machine's storage. Its own object so it can grow (premium, data disks)."""
    size_gb: int = 30
    type: str = "standard"


@dataclass
class Machine:
    """One VM: hardware + storage + the workload that runs on it.

    `domain` lives here, not on Infrastructure: the DNS A record points at this
    machine's IP and Caddy (TLS + reverse_proxy) runs on this machine. With
    several machines, each can carry its own domain.
    """
    # The resolved VMSpec. May be given as an ExpectedSpecs (a request) at input
    # time; deploy()/the TUI resolve it into a concrete VMSpec before provisioning.
    hardware: "ExpectedSpecs | VMSpec" = field(default_factory=ExpectedSpecs)
    disk: Disk = field(default_factory=Disk)
    ship: list[str] = field(default_factory=list)       # directories to rsync over
    setup: list[str] = field(default_factory=list)       # run once, must exit
    start: Optional[str] = None                          # long-running service
    ports: list[int] = field(default_factory=list)       # app ports to expose
    domain: Optional["Domain"] = None


@dataclass
class Infrastructure:
    """The whole thing you're deploying: a container of machines."""
    name: str = "default"
    location: str = "CentralUS"
    machines: list[Machine] = field(default_factory=lambda: [Machine()])


# --- Live state (output) -----------------------------------------------------
# What you got back from a deploy.

@dataclass
class Service:
    port: int
    url: str


@dataclass
class Deployment:
    name: str
    ip: str
    ssh_key: str
    user: str = "azureuser"
    services: list[Service] = field(default_factory=list)

    @property
    def url(self) -> Optional[str]:
        return self.services[0].url if self.services else None

    @property
    def ssh_command(self) -> str:
        return f"ssh -i {self.ssh_key} {self.user}@{self.ip}"
