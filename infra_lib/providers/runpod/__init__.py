"""RunPod provider: a GPU container host.

RunPod doesn't give you a VM to configure — it boots a container image you hand
it (via Pulumi) and returns an HTTPS proxy URL. So it realizes `pod` units; the
image is required at create time, and ship/setup run only when the pod exposes
SSH. See provider.py / provision.py.
"""
