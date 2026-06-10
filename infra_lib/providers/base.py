"""The provider interface.

A Provider is the seam between provider-agnostic orchestration (pipeline.py,
core/) and a specific cloud. `pipeline.deploy()` and the CLI talk to a Provider;
they never import a cloud module directly. To add a cloud, implement this
interface and register it in `providers/__init__.py`.

Implementations are thin: the real work lives in the provider's own modules
(e.g. providers/azure/{provision,sizes,auth}.py); the Provider just exposes a
uniform surface over them.

deploy() runs the same ordered steps for every unit — create -> ship -> setup ->
start -> expose -> health — and a step no-ops when the substrate can't do it.
The provider owns the steps that differ by substrate: create() (provision),
start() (vm: systemd / pod: already running) and expose() (vm: Caddy+DNS / pod:
the proxy URL it already has). ship/setup are shared SSH code the pipeline runs
when the returned Endpoint has SSH.
"""
from abc import ABC, abstractmethod

from ..models import ExpectedSpecs, VMSpec, Endpoint, Unit


class Provider(ABC):
    # --- identity / vocabulary -------------------------------------------------
    name: str = ""              # registry key, e.g. "azure"
    admin_user: str = ""        # default admin/SSH user on created units
    size_term: str = "size"     # human term for an exact instance id (UI labels)
    presets: dict = {}          # {label: {"cpu", "ram_gb", "price", ...}}
    unit_type: str = "vm"       # the unit `type` this provider realizes: "vm" | "pod"

    # --- sizing ----------------------------------------------------------------
    @abstractmethod
    def preset_specs(self, label: str) -> ExpectedSpecs:
        """Turn a preset label ('small') into the cpu/ram minimums it implies."""

    @abstractmethod
    def resolve(self, request, location: str) -> VMSpec:
        """Turn a sizing request (ExpectedSpecs or exact VMSpec) into a concrete,
        available VMSpec. Idempotent on an already-resolved VMSpec."""

    @abstractmethod
    def list_sizes(self, location: str, min_cpu: int = 0, min_ram_gb: float = 0,
                   gpu: int = 0, gpu_type: str = None) -> list[dict]:
        """Available sizes as [{name, cpu, ram_gb, gpus, price}], cheapest first.
        `gpu`/`gpu_type` filter to GPU sizes. Used by the `sizes` command;
        `resolve` is the path used by deploy."""

    # --- pipeline steps --------------------------------------------------------
    @abstractmethod
    def create(self, name: str, location: str, ssh_key_path: str, unit: Unit) -> Endpoint:
        """Provision the unit and return a reachable Endpoint.

        For a vm this brings up a box and waits until it accepts SSH. For a pod it
        creates the container from `unit.image` and returns its proxy URL (and an
        SSH endpoint when the pod exposes one). The returned Endpoint's `has_ssh`
        decides whether the pipeline runs ship/setup."""

    def start(self, endpoint: Endpoint, name: str, unit: Unit) -> None:
        """Bring up `unit.start` as a supervised service.

        Default no-op: a pod's command is its container CMD, already running from
        create(). A vm overrides this to install a systemd service over SSH."""

    def expose(self, endpoint: Endpoint, name: str, unit: Unit) -> str | None:
        """Make the app reachable and return its public URL.

        Default returns the URL the Endpoint already carries (a pod's proxy URL).
        A vm overrides this to configure Caddy (TLS + reverse_proxy) and DNS."""
        return endpoint.url

    @abstractmethod
    def destroy(self, name: str, purge: bool = True) -> None:
        """Tear down a deployment created by this provider."""

    @abstractmethod
    def list_deployments(self) -> list[dict]:
        """All deployments as [{name, ip, url, ssh_key}]."""

    # --- power (optional) ------------------------------------------------------
    # Stop/resume a deployment without destroying it. Optional: providers that
    # can't do this leave the defaults, which report it isn't supported.
    def pause(self, name: str) -> None:
        """Stop compute billing while keeping the disk/volume (resume later)."""
        raise NotImplementedError(f"The {self.name} provider doesn't support pause.")

    def resume(self, name: str) -> None:
        """Start a previously paused deployment back up."""
        raise NotImplementedError(f"The {self.name} provider doesn't support resume.")

    # --- auth ------------------------------------------------------------------
    @abstractmethod
    def load_credentials(self) -> None:
        """Load saved credentials into the environment (raise if none)."""

    @abstractmethod
    def authenticate(self, **kwargs) -> None:
        """Interactive first-time auth (e.g. device-code flow)."""

    @abstractmethod
    def save_credentials(self, **kwargs) -> str:
        """Non-interactive auth: persist existing credentials. Returns the path."""
