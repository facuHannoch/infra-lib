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
    on a Unit — it's only an input that produces a VMSpec.

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

    This is what a Unit stores. Produced by resolving an ExpectedSpecs (cheapest
    that satisfies it) or by naming an exact `type` directly. `type` is the provider
    instance id (Azure SKU, e.g. 'Standard_D2s_v3'; a RunPod GPU id); cpu/ram/price
    are filled in by resolution and used for display.
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
    """A unit's storage. Its own object so it can grow (premium, data disks)."""
    size_gb: int = 30
    type: str = "standard"


@dataclass
class Endpoint:
    """How to reach a created unit. Returned by a provider's create().

    Generalizes "a public IP you SSH into on port 22 as azureuser": a RunPod pod
    is reached on a different host/port as root (no sudo), may expose no SSH at
    all, and already knows its public URL. The shared transfer/health/start code
    keys its behavior off this — e.g. ship/setup run only when `has_ssh`.
    """
    host: str
    user: str = "azureuser"
    ssh_port: int = 22
    sudo: bool = True
    has_ssh: bool = True
    ssh_key: Optional[str] = None      # private key path used to reach it over SSH
    url: Optional[str] = None          # known at create time for pods (proxy URL)
    handle: Optional[str] = None       # provider's id for management (defaults to the name)

    @property
    def home(self) -> str:
        """The login user's home dir, for expanding `~` in ship destinations."""
        return "/root" if self.user == "root" else f"/home/{self.user}"


@dataclass
class ShipItem:
    """One `ship` entry: a local source dir and where it lands on the unit.

    `dest` is None for the default location (/srv/files/<basename>); otherwise an
    absolute or ~-relative remote path the source's contents are placed *at*.
    """
    src: str
    dest: Optional[str] = None


@dataclass
class Unit:
    """One unit of deployment and the work that runs on it.

    A unit has a `type` — `vm` (a box we fill: ship + setup + a systemd `start`)
    or `pod` (a container host like RunPod that boots from `image`). `type` is a
    realization detail, not a separate object: the deploy pipeline runs the same
    ordered steps for every unit and a step no-ops when the substrate can't do it
    (e.g. a pod with no SSH skips ship/setup). See pipeline.deploy().

    Fields are a superset; which ones apply depends on `type`:
      - both:  resources (hardware/disk/ports), setup, start, env, domain
      - vm:    ship (rsync), start -> systemd service
      - pod:   image (required — it boots from this), start -> container CMD

    `domain` lives here, not on Infrastructure: the DNS A record points at this
    unit and Caddy (TLS + reverse_proxy) runs on it. With several units, each can
    carry its own domain.
    """
    name: Optional[str] = None                           # identifies the unit (nested config)
    type: str = "vm"                                     # vm | pod (the discriminator)
    # The resolved VMSpec. May be given as an ExpectedSpecs (a request) at input
    # time; deploy()/the TUI resolve it into a concrete VMSpec before creating.
    hardware: "ExpectedSpecs | VMSpec" = field(default_factory=ExpectedSpecs)
    disk: Disk = field(default_factory=Disk)
    ship: list[ShipItem] = field(default_factory=list)   # dirs to rsync (where SSH is available)
    setup: list[str] = field(default_factory=list)       # run once, must exit (where SSH is available)
    start: Optional[str] = None                          # vm: systemd service; pod: container CMD
    ports: list[int] = field(default_factory=list)       # app ports to expose
    domain: Optional["Domain"] = None
    env: dict = field(default_factory=dict)              # env vars (systemd Environment= / pod env)
    # --- pod / image ---
    image: Optional[str] = None                          # image to boot, e.g. ghcr.io/me/app:latest
    build: Optional[str] = None                          # dir with a Dockerfile to build + push
    registry: Optional[str] = None                       # push target for `build`, e.g. ghcr.io/me


@dataclass
class Infrastructure:
    """The whole thing you're deploying: a container of units.

    `provider` names the cloud this lands on (see infra_lib.providers); it's an
    infra-level choice, while sizing/disk/ports/etc. are per-Unit. By default the
    provider is derived from the unit's `type` (vm -> azure, pod -> runpod).
    """
    name: str = "default"
    location: str = "CentralUS"
    provider: str = "azure"
    units: list[Unit] = field(default_factory=lambda: [Unit()])


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
