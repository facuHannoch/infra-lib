"""AzureProvider: the Provider interface over the azure/* modules.

A thin facade — the implementation lives in provision.py, sizes.py, auth.py.
Azure-specific details (the 'azureuser' admin user, the 'SKU' size term, the
ARM_* credential env vars) are declared/owned here, not leaked into core/.
"""
import os

from ..base import Provider
from ...models import ExpectedSpecs, VMSpec
from . import sizes as _sizes
from . import auth as _auth

# provision.py pulls in Pulumi (heavy); import it lazily so that merely
# selecting the provider — for sizing, auth, or config parsing — stays cheap.


class AzureProvider(Provider):
    name = "azure"
    admin_user = "azureuser"
    size_term = _sizes.SIZE_TERM
    presets = _sizes.AZURE_PRESETS

    def preset_specs(self, label: str) -> ExpectedSpecs:
        return _sizes.expectedspecs_from_preset(label)

    def resolve(self, request, location: str) -> VMSpec:
        return _sizes.resolve(request, location)

    def _credential(self):
        from azure.identity import ClientSecretCredential
        return ClientSecretCredential(
            tenant_id=os.environ["ARM_TENANT_ID"],
            client_id=os.environ["ARM_CLIENT_ID"],
            client_secret=os.environ["ARM_CLIENT_SECRET"],
        )

    def list_sizes(self, location: str, min_cpu: int = 0, min_ram_gb: float = 0,
                   gpu: int = 0, gpu_type: str = None) -> list[dict]:
        self.load_credentials()
        specs = _sizes._azure_size_specs(location, self._credential())
        prices = _sizes._azure_list_sizes(location)
        token = _sizes._gpu_token(gpu_type) if gpu_type else None
        out = []
        for s in specs:
            if s["cpu"] < min_cpu or s["ram_gb"] < min_ram_gb or s["name"] not in prices:
                continue
            if (gpu or gpu_type) and s.get("gpus", 0) < max(gpu, 1):
                continue
            if token and token not in s["name"]:
                continue
            out.append({"name": s["name"], "cpu": s["cpu"], "ram_gb": s["ram_gb"],
                        "gpus": s.get("gpus", 0), "price": prices[s["name"]]})
        out.sort(key=lambda s: s["price"])
        return out

    def provision(self, name: str, location: str, ssh_key_path: str,
                  vm_spec: VMSpec, storage_gb: int = 30) -> dict:
        from . import provision as _provision
        return _provision.provision(
            name=name, location=location, ssh_key_path=ssh_key_path,
            vm_spec=vm_spec, storage_gb=storage_gb,
        )

    def destroy(self, name: str, purge: bool = True) -> None:
        from . import provision as _provision
        _provision.destroy(name, purge=purge)

    def list_deployments(self) -> list[dict]:
        from . import provision as _provision
        return _provision.list_deployments()

    def pause(self, name: str) -> None:
        from . import provision as _provision
        _provision.pause(name)

    def resume(self, name: str) -> None:
        from . import provision as _provision
        _provision.resume(name)

    def load_credentials(self) -> None:
        _auth.load_azure_credentials()

    def authenticate(self, **kwargs) -> None:
        _auth.auth_azure()

    def save_credentials(self, **kwargs) -> str:
        return _auth.save_azure_credentials(**kwargs)
