from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .core.domain import Domain


# --- Desired state (input) ---------------------------------------------------
# What you want. Maps 1:1 to infra.yml. Passed to deploy() -> Deployment.

@dataclass
class ExpectedSpecs:
    """What you ask for: minimum cpu/ram (+ optional GPU). Transient, provider-agnostic.

    Resolved into a concrete VMSpec by the provider (see resolve()). Never stored
    on a Machine — it's only an input that produces a VMSpec.

    GPU is an orthogonal axis: a GPU SKU bundles its own cpu/ram, so when `gpu`
    is requested the cpu/ram fields act as additional minimums (usually moot).
    `gpu_type` is a friendly name ("t4", "a10", "a100"); None means any GPU.
    """
    cpu: int = 2
    ram_gb: float = 8
    gpu: int = 0
    gpu_type: Optional[str] = None


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
    gpus: int = 0
    price_per_hour: Optional[float] = None

    def __str__(self):
        price = f"~${self.price_per_hour:.4f}/hr" if self.price_per_hour else "price unknown"
        gpu = f", {self.gpus}x GPU" if self.gpus else ""
        return f"{self.type} ({self.cpu} vCPU, {self.ram_gb}GB RAM{gpu}, {price})"


@dataclass
class Disk:
    """A machine's storage. Its own object so it can grow (premium, data disks)."""
    size_gb: int = 30
    type: str = "standard"


@dataclass
class Endpoint:
    """How to reach a provisioned host. Returned by a provider's provision().

    Generalizes "a public IP you SSH into on port 22 as azureuser": a RunPod pod
    is reached on a different host/port as root, and is already root (no sudo).
    Lets the shared transfer/health code work against any provider's box.
    """
    host: str
    user: str = "azureuser"
    ssh_port: int = 22
    sudo: bool = True


@dataclass
class Machine:
    """One machine and the workload that runs on it.

    Two workload shapes (see `is_container`):
      - process:   ship a directory, run setup, supervise `start` via systemd. VM-only.
      - container: run an image (`image` ref, or `build` a Dockerfile dir and push
                   to `registry`). Runs on a container host (RunPod) or a VM w/ Docker.
    They're mutually exclusive; presence of image/build selects the container shape.

    `domain` lives here, not on Infrastructure: the DNS A record points at this
    machine's IP and Caddy (TLS + reverse_proxy) runs on this machine. With
    several machines, each can carry its own domain.
    """
    name: Optional[str] = None                           # identifies the machine (nested config)
    # The resolved VMSpec. May be given as an ExpectedSpecs (a request) at input
    # time; deploy()/the TUI resolve it into a concrete VMSpec before provisioning.
    hardware: "ExpectedSpecs | VMSpec" = field(default_factory=ExpectedSpecs)
    disk: Disk = field(default_factory=Disk)
    ship: list[str] = field(default_factory=list)       # directories to rsync over (process)
    setup: list[str] = field(default_factory=list)       # run once, must exit (process)
    start: Optional[str] = None                          # process: systemd service; container: CMD override
    ports: list[int] = field(default_factory=list)       # app ports to expose
    domain: Optional["Domain"] = None
    # --- container workload ---
    image: Optional[str] = None                          # image ref to run, e.g. ghcr.io/me/app:latest
    build: Optional[str] = None                          # dir with a Dockerfile to build + push
    registry: Optional[str] = None                       # push target for `build`, e.g. ghcr.io/me
    env: dict = field(default_factory=dict)              # env vars for the container

    @property
    def is_container(self) -> bool:
        return bool(self.image or self.build)


@dataclass
class Infrastructure:
    """The whole thing you're deploying: a container of machines.

    `provider` names the cloud this lands on (see infra_lib.providers); it's an
    infra-level choice, while sizing/disk/ports/etc. are per-Machine.
    """
    name: str = "default"
    location: str = "CentralUS"
    provider: str = "azure"
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
