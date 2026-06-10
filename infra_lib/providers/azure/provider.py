"""AzureProvider: the Provider interface over the azure/* modules.

A thin facade — the implementation lives in provision.py, sizes.py, auth.py.
Azure-specific details (the 'azureuser' admin user, the 'SKU' size term, the
ARM_* credential env vars, Caddy + cloud-init) are owned here, not leaked into
core/. Azure realizes `vm` units: a box we fill over SSH (ship + setup + a
systemd `start`) and expose with Caddy + DNS.
"""
import os

from ..base import Provider
from ...models import ExpectedSpecs, VMSpec, Endpoint, Unit
from ... import progress
from . import sizes as _sizes
from . import auth as _auth

# provision.py pulls in Pulumi (heavy); import it lazily so that merely
# selecting the provider — for sizing, auth, or config parsing — stays cheap.


class AzureProvider(Provider):
    name = "azure"
    admin_user = "azureuser"
    size_term = _sizes.SIZE_TERM
    presets = _sizes.AZURE_PRESETS
    unit_type = "vm"

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

    # --- pipeline steps --------------------------------------------------------
    def create(self, name: str, location: str, ssh_key_path: str, unit: Unit) -> Endpoint:
        from . import provision as _provision
        from ...core.transfer import open_ssh, wait_for_cloud_init
        outputs = _provision.provision(
            name=name, location=location, ssh_key_path=ssh_key_path,
            vm_spec=unit.hardware, storage_gb=unit.disk.size_gb,
        )
        ip = outputs["public_ip"]
        progress.reporter().show_ip(ip)
        # The box is up but not yet ready: wait for SSH, then for cloud-init to
        # finish installing Caddy (so expose() can configure it).
        open_ssh(ip, ssh_key_path, wait=True, user=self.admin_user).close()
        wait_for_cloud_init(ip, ssh_key_path, self.admin_user)
        return Endpoint(host=ip, user=self.admin_user, ssh_port=22, sudo=True,
                        has_ssh=True, ssh_key=ssh_key_path, handle=name)

    def start(self, endpoint: Endpoint, name: str, unit: Unit) -> None:
        if not unit.start:
            return
        from ...core.transfer import start_service
        start_service(endpoint.host, name, unit.start, ssh_key_path=endpoint.ssh_key,
                      user=endpoint.user, port=endpoint.ssh_port, env=unit.env)

    def expose(self, endpoint: Endpoint, name: str, unit: Unit) -> str | None:
        from ...core.transfer import configure_caddy
        from ...core.domain import default_caddyfile
        r = progress.reporter()
        ip = endpoint.host
        port = unit.ports[0] if unit.ports else None
        domain = unit.domain
        has_content = bool(unit.ship or port)

        if domain:
            if domain.auto_dns:
                r.step(f"Provisioning DNS for {domain.name}")
                domain.provision_dns(ip)
                r.done("DNS configured")
            else:
                r.need_dns(domain, ip)

        caddyfile = domain.caddyfile(port=port) if domain else (
            default_caddyfile(port=port) if has_content else None)
        if caddyfile:
            r.step("Configuring web server")
            configure_caddy(ip, caddyfile, ssh_key_path=endpoint.ssh_key,
                            user=endpoint.user, port=endpoint.ssh_port)
            r.done("Caddy configured")

        return domain.url() if domain else (f"http://{ip}" if has_content else None)

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
