"""RunPod provisioning via the Pulumi Automation API.

Mirrors azure/provision.py: Pulumi owns the lifecycle (create/destroy/list) of a
`pulumi_runpod.Pod`; the runpod SDK is used only for things Pulumi doesn't model
— reading the pod's runtime SSH port mapping, and pause/resume (stop/start). A
separate Pulumi project keeps RunPod stacks from colliding with Azure's.

NOTE: the live paths here need a RunPod API key + GPU availability + the
`pulumi-runpod` and `runpod` packages; they're written against the documented
APIs and verified structurally only.
"""
import os
from pulumi import automation as auto

from ... import progress

# Separate project: list_stacks() is per-project, so Azure and RunPod deployments
# don't appear in each other's listings.
_PROJECT = "infra-lib-runpod"


def _env_vars() -> dict:
    # The pulumi-runpod provider authenticates from RUNPOD_API_KEY (set by
    # auth.load_runpod_key); the passphrase keeps the local secrets provider quiet.
    return {
        "PULUMI_CONFIG_PASSPHRASE": "",
        "RUNPOD_API_KEY": os.environ.get("RUNPOD_API_KEY", ""),
    }


def _program(name, image, gpu_type_id, gpu_count, ports_str, env, volume_gb,
             container_gb, command, start_ssh, primary_port):
    def _p():
        import pulumi
        import pulumi_runpod as runpod
        pod = runpod.Pod(
            "pod",
            name=name,
            image_name=image,
            gpu_type_id=gpu_type_id,
            gpu_count=gpu_count,
            cloud_type="ALL",
            ports=ports_str,
            env=[{"key": k, "value": str(v)} for k, v in (env or {}).items()],
            volume_in_gb=volume_gb,
            container_disk_in_gb=container_gb,
            docker_args=command or "",
            start_ssh=start_ssh,
        )
        pulumi.export("pod_id", pod.id)
        if primary_port:
            pulumi.export("url", pod.id.apply(
                lambda pid: f"https://{pid}-{primary_port}.proxy.runpod.net"))
    return _p


def create(name, vm_spec, image, ports, env, command, storage_gb,
           start_ssh) -> tuple[str, str | None]:
    from .auth import load_runpod_key
    load_runpod_key()

    http_ports = [int(p) for p in (ports or [])]
    spec = [f"{p}/http" for p in http_ports]
    if start_ssh:
        spec.append("22/tcp")
    ports_str = ",".join(spec)
    primary_port = http_ports[0] if http_ports else None

    stack = auto.create_or_select_stack(
        stack_name=name,
        project_name=_PROJECT,
        program=_program(name, image, vm_spec.type, max(vm_spec.gpus, 1), ports_str,
                         env, storage_gb, max(storage_gb, 20), command, start_ssh,
                         primary_port),
        opts=auto.LocalWorkspaceOptions(env_vars=_env_vars()),
    )

    progress.step(f"Creating {vm_spec.type} pod for {image}")
    from pulumi.automation.errors import ConcurrentUpdateError
    try:
        result = stack.up(on_output=progress.raw)
    except ConcurrentUpdateError:
        stack.cancel()
        result = stack.up(on_output=progress.raw)
    outputs = {k: v.value for k, v in result.outputs.items()}
    progress.done("Pod created")
    return outputs.get("pod_id"), outputs.get("url")


def ssh_endpoint(pod_id: str) -> tuple[str | None, int | None]:
    """Best-effort (host, port) for SSH into a running pod, or (None, None).

    RunPod publishes the 22/tcp mapping in the pod's runtime once it's up. If we
    can't read it (not up yet, no public mapping), the caller treats the pod as
    SSH-less and skips ship/setup.
    """
    try:
        import runpod
        from .auth import load_runpod_key
        load_runpod_key()
        pod = runpod.get_pod(pod_id) or {}
        for p in (pod.get("runtime") or {}).get("ports") or []:
            if p.get("type") == "tcp" and int(p.get("privatePort", 0)) == 22 and p.get("isIpPublic"):
                return p.get("ip"), int(p.get("publicPort"))
    except Exception as e:
        progress.raw(f"  (could not resolve pod SSH endpoint: {e})")
    return None, None


def _stack_outputs(name: str) -> dict:
    stack = auto.select_stack(
        stack_name=name, project_name=_PROJECT, program=lambda: None,
        opts=auto.LocalWorkspaceOptions(env_vars=_env_vars()),
    )
    return {k: v.value for k, v in stack.outputs().items()}


def destroy(name: str, purge: bool = True):
    from pulumi.automation.errors import ConcurrentUpdateError
    stack = auto.select_stack(
        stack_name=name, project_name=_PROJECT, program=lambda: None,
        opts=auto.LocalWorkspaceOptions(env_vars=_env_vars()),
    )
    try:
        stack.destroy(on_output=progress.raw)
    except ConcurrentUpdateError:
        stack.cancel()
        stack.destroy(on_output=progress.raw)
    if purge:
        stack.workspace.remove_stack(name)


def list_deployments() -> list:
    ws = auto.LocalWorkspace(
        project_settings=auto.ProjectSettings(name=_PROJECT, runtime="python"),
        env_vars=_env_vars(),
    )
    result = []
    for summary in ws.list_stacks():
        try:
            outputs = _stack_outputs(summary.name)
            result.append({
                "name": summary.name,
                "ip": outputs.get("pod_id", "-"),
                "url": outputs.get("url", "-"),
                "ssh_key": "-",
            })
        except Exception:
            result.append({"name": summary.name, "ip": "-", "url": "-", "ssh_key": "-"})
    return result


def _power(name: str, action: str, start_msg: str, done_msg: str):
    """Drive pod stop/resume via the runpod SDK (Pulumi doesn't model power)."""
    import runpod
    from .auth import load_runpod_key
    load_runpod_key()
    pod_id = _stack_outputs(name).get("pod_id")
    if not pod_id:
        raise RuntimeError(f"Deployment '{name}' didn't record a pod id.")
    progress.step(start_msg)
    if action == "stop":
        runpod.stop_pod(pod_id)
    else:
        runpod.resume_pod(pod_id, gpu_count=1)
    progress.done(done_msg)


def pause(name: str):
    _power(name, "stop", f"Pausing {name}", f"{name} paused — GPU released")


def resume(name: str):
    _power(name, "resume", f"Resuming {name}", f"{name} resumed")
