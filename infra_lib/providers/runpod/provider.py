"""RunPodProvider: a GPU container host behind the Provider interface.

RunPod realizes `pod` units: it boots a container from `unit.image` and returns a
proxy URL. Provisioning goes through Pulumi (see provision.py), exactly like
Azure — so create/destroy/list share that shape. start()/expose() use the base
no-op/return-URL defaults: a pod's command is its container CMD (set at create)
and its URL is the proxy URL. ship/setup work only when the pod exposes SSH
(start_ssh + a readable 22/tcp mapping); otherwise the pipeline skips them.
"""
from ..base import Provider
from ...models import ExpectedSpecs, VMSpec, Endpoint, Unit
from ... import progress
from . import sizes as _sizes
from . import auth as _auth


class RunPodProvider(Provider):
    name = "runpod"
    admin_user = "root"           # containers run as root (no sudo)
    size_term = _sizes.SIZE_TERM
    presets = _sizes.RUNPOD_PRESETS
    unit_type = "pod"
    gpu_first = True

    # --- sizing ----------------------------------------------------------------
    def preset_specs(self, label: str) -> ExpectedSpecs:
        return _sizes.expectedspecs_from_preset(label)

    def resolve(self, request, location: str = None) -> VMSpec:
        return _sizes.resolve(request, location)

    def list_sizes(self, location: str = None, min_cpu: int = 0, min_ram_gb: float = 0,
                   gpu: int = 0, gpu_type: str = None) -> list[dict]:
        return _sizes.list_sizes(min_cpu=min_cpu, min_ram_gb=min_ram_gb,
                                 gpu=gpu, gpu_type=gpu_type)

    # --- pipeline steps --------------------------------------------------------
    def create(self, name: str, location: str, ssh_key_path: str, unit: Unit) -> Endpoint:
        from . import provision as _provision
        # Enable SSH only when there's something to ship/run over it. Inject our
        # public key so we're authorized on the pod.
        want_ssh = bool(unit.ship or unit.setup)
        env = dict(unit.env)
        if want_ssh:
            with open(f"{ssh_key_path}.pub") as f:
                env["PUBLIC_KEY"] = f.read().strip()

        pod_id, url = _provision.create(
            name=name, vm_spec=unit.hardware, image=unit.image, ports=unit.ports,
            env=env, command=unit.start, storage_gb=unit.disk.size_gb, start_ssh=want_ssh,
        )

        host, port, has_ssh = pod_id, 22, False
        if want_ssh:
            ssh_host, ssh_port = _provision.ssh_endpoint(pod_id)
            if ssh_host:
                host, port, has_ssh = ssh_host, ssh_port, True
            else:
                progress.reporter().warn(
                    "Pod SSH endpoint not available yet — skipping ship/setup. "
                    "Bake files into the image, or retry once the pod is fully up.")

        if url:
            progress.done(f"Pod running: {url}")
        return Endpoint(host=host, user=self.admin_user, ssh_port=port, sudo=False,
                        has_ssh=has_ssh, ssh_key=ssh_key_path, url=url, handle=pod_id)

    # start()/expose() use the base defaults (no-op / return endpoint.url).

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

    # --- auth ------------------------------------------------------------------
    def load_credentials(self) -> None:
        _auth.load_runpod_key()

    def authenticate(self, **kwargs) -> None:
        _auth.auth_runpod()

    def save_credentials(self, **kwargs) -> str:
        return _auth.save_runpod_key(api_key=kwargs.get("api_key"))
