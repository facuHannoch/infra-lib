"""The provider interface.

A Provider is the seam between provider-agnostic orchestration (pipeline.py,
core/) and a specific cloud. `pipeline.deploy()` and the CLI talk to a Provider;
they never import a cloud module directly. To add a cloud, implement this
interface and register it in `providers/__init__.py`.

Implementations are thin: the real work lives in the provider's own modules
(e.g. providers/azure/{provision,sizes,auth}.py); the Provider just exposes a
uniform surface over them.
"""
from abc import ABC, abstractmethod

from ..models import ExpectedSpecs, VMSpec


class Provider(ABC):
    # --- identity / vocabulary -------------------------------------------------
    name: str = ""              # registry key, e.g. "azure"
    admin_user: str = ""        # default admin/SSH user on provisioned VMs
    size_term: str = "size"     # human term for an exact instance id (UI labels)
    presets: dict = {}          # {label: {"cpu", "ram_gb", "price", ...}}

    # --- sizing ----------------------------------------------------------------
    @abstractmethod
    def preset_specs(self, label: str) -> ExpectedSpecs:
        """Turn a preset label ('small') into the cpu/ram minimums it implies."""

    @abstractmethod
    def resolve(self, request, location: str) -> VMSpec:
        """Turn a sizing request (ExpectedSpecs or exact VMSpec) into a concrete,
        available VMSpec. Idempotent on an already-resolved VMSpec."""

    @abstractmethod
    def list_sizes(self, location: str, min_cpu: int = 0, min_ram_gb: float = 0) -> list[dict]:
        """Available sizes as [{name, cpu, ram_gb, price}], cheapest first.
        Used by the `sizes` command; `resolve` is the path used by deploy."""

    # --- provisioning ----------------------------------------------------------
    @abstractmethod
    def provision(self, name: str, location: str, ssh_key_path: str,
                  vm_spec: VMSpec, storage_gb: int = 30) -> dict:
        """Create the infrastructure; return outputs (must include 'public_ip')."""

    @abstractmethod
    def destroy(self, name: str, purge: bool = True) -> None:
        """Tear down a deployment created by this provider."""

    @abstractmethod
    def list_deployments(self) -> list[dict]:
        """All deployments as [{name, ip, url, ssh_key}]."""

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
