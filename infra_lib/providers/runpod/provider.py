"""RunPodProvider: a GPU container host behind the Provider interface.

RunPod runs a container image you give it and returns an HTTPS proxy URL, so it's
a `kind == "container_host"` provider — deploy() calls launch() instead of the
VM pipeline. The `runpod` SDK is imported lazily so selecting the provider for
sizing/auth stays cheap.

NOTE: the live API calls below can't be exercised without an API key + GPU quota;
they're written against the runpod SDK and verified structurally only.
"""
from ..base import Provider
from ...models import ExpectedSpecs, VMSpec
from ... import progress
from . import sizes as _sizes
from . import auth as _auth


class RunPodProvider(Provider):
    name = "runpod"
    admin_user = "root"           # containers run as root
    size_term = _sizes.SIZE_TERM
    presets = _sizes.RUNPOD_PRESETS
    kind = "container_host"
    workloads = {"container"}

    # --- sizing ----------------------------------------------------------------
    def preset_specs(self, label: str) -> ExpectedSpecs:
        return _sizes.expectedspecs_from_preset(label)

    def resolve(self, request, location: str = None) -> VMSpec:
        return _sizes.resolve(request, location)

    def list_sizes(self, location: str = None, min_cpu: int = 0, min_ram_gb: float = 0,
                   gpu: int = 0, gpu_type: str = None) -> list[dict]:
        return _sizes.list_sizes(min_cpu=min_cpu, min_ram_gb=min_ram_gb,
                                 gpu=gpu, gpu_type=gpu_type)

    # --- container host --------------------------------------------------------
    def launch(self, name: str, vm_spec: VMSpec, image: str, ports: list,
               env: dict = None, command: str = None, storage_gb: int = 30) -> dict:
        import runpod
        _auth.load_runpod_key()

        http_ports = list(ports) if ports else [80]
        ports_str = ",".join(f"{p}/http" for p in http_ports)

        progress.step(f"Launching {vm_spec.type} pod for {image}")
        pod = runpod.create_pod(
            name=name,
            image_name=image,
            gpu_type_id=vm_spec.type,
            gpu_count=max(vm_spec.gpus, 1),
            cloud_type="ALL",
            # volume persists across stop/resume; container disk holds the image.
            volume_in_gb=storage_gb,
            container_disk_in_gb=max(storage_gb, 20),
            ports=ports_str,
            env=env or {},
            docker_args=command or "",
        )
        pod_id = pod["id"]
        self._wait_running(pod_id)

        # RunPod exposes each http port at a stable proxy hostname.
        url = f"https://{pod_id}-{http_ports[0]}.proxy.runpod.net"
        progress.done(f"Pod running: {url}")
        return {"url": url, "handle": pod_id, "ip": pod_id}

    def _wait_running(self, pod_id: str, timeout: int = 300):
        import time
        import runpod
        deadline = time.time() + timeout
        with progress.status("Waiting for pod to start..."):
            while time.time() < deadline:
                pod = runpod.get_pod(pod_id)
                if pod and pod.get("runtime"):
                    return
                time.sleep(5)
        raise TimeoutError(f"RunPod pod {pod_id} did not start within {timeout}s")

    # --- lifecycle / management ------------------------------------------------
    def _find_pod(self, name: str):
        import runpod
        _auth.load_runpod_key()
        for p in runpod.get_pods():
            if p.get("name") == name:
                return p
        return None

    def provision(self, *args, **kwargs):
        raise NotImplementedError("RunPod is a container host — deploy() uses launch().")

    def destroy(self, name: str, purge: bool = True) -> None:
        import runpod
        pod = self._find_pod(name)
        if not pod:
            raise RuntimeError(f"No RunPod pod named '{name}'.")
        runpod.terminate_pod(pod["id"])

    def list_deployments(self) -> list[dict]:
        import runpod
        _auth.load_runpod_key()
        out = []
        for p in runpod.get_pods():
            out.append({"name": p.get("name", "-"), "ip": p.get("id", "-"),
                        "url": "-", "ssh_key": "-"})
        return out

    def pause(self, name: str) -> None:
        import runpod
        pod = self._find_pod(name)
        if not pod:
            raise RuntimeError(f"No RunPod pod named '{name}'.")
        progress.step(f"Pausing {name}")
        runpod.stop_pod(pod["id"])
        progress.done(f"{name} paused — GPU released")

    def resume(self, name: str) -> None:
        import runpod
        pod = self._find_pod(name)
        if not pod:
            raise RuntimeError(f"No RunPod pod named '{name}'.")
        progress.step(f"Resuming {name}")
        runpod.resume_pod(pod["id"], gpu_count=1)
        progress.done(f"{name} resumed")

    # --- auth ------------------------------------------------------------------
    def load_credentials(self) -> None:
        _auth.load_runpod_key()

    def authenticate(self, **kwargs) -> None:
        _auth.auth_runpod()

    def save_credentials(self, **kwargs) -> str:
        return _auth.save_runpod_key(api_key=kwargs.get("api_key"))
