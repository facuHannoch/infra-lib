"""RunPod provider: a container host with GPUs.

RunPod doesn't give you a VM to configure — it runs a container image you hand
it and returns an HTTPS proxy URL. So it's a `kind == "container_host"` provider
and only supports the `container` workload.
"""
