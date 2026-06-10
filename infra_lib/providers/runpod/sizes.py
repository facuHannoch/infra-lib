"""RunPod sizing — GPU-first.

On RunPod you pick a GPU, not a CPU SKU; vCPU/RAM come bundled. So a VMSpec's
`type` here is the RunPod GPU id (e.g. "NVIDIA A40"), and the friendly `gpu_type`
from ExpectedSpecs ("a40", "l40s", ...) selects it.
"""
from ...models import ExpectedSpecs, VMSpec

SIZE_TERM = "GPU"

# Friendly name -> RunPod GPU id + bundled specs. Prices are approximate
# community-cloud on-demand rates (USD/hr); live values come from the API.
RUNPOD_GPUS = {
    "a40":        {"id": "NVIDIA A40",                       "cpu": 9,  "ram_gb": 50,  "vram_gb": 48,  "price": 0.44},
    "rtxa6000":   {"id": "NVIDIA RTX A6000",                 "cpu": 9,  "ram_gb": 50,  "vram_gb": 48,  "price": 0.49},
    "rtx6000ada": {"id": "NVIDIA RTX 6000 Ada Generation",   "cpu": 10, "ram_gb": 167, "vram_gb": 48,  "price": 0.77},
    "l40s":       {"id": "NVIDIA L40S",                      "cpu": 16, "ram_gb": 94,  "vram_gb": 48,  "price": 0.86},
    "l40":        {"id": "NVIDIA L40",                       "cpu": 8,  "ram_gb": 94,  "vram_gb": 48,  "price": 0.99},
    "a100":       {"id": "NVIDIA A100 80GB PCIe",            "cpu": 8,  "ram_gb": 117, "vram_gb": 80,  "price": 1.64},
    "h100":       {"id": "NVIDIA H100 80GB HBM3",            "cpu": 16, "ram_gb": 188, "vram_gb": 80,  "price": 2.99},
}

# Presets (for --vm choices / TUI): each is a GPU label.
RUNPOD_PRESETS = {
    label: {"cpu": g["cpu"], "ram_gb": g["ram_gb"], "price": g["price"]}
    for label, g in RUNPOD_GPUS.items()
}

_DEFAULT_GPU = "a40"


def _by_id(gpu_id: str):
    for g in RUNPOD_GPUS.values():
        if g["id"] == gpu_id:
            return g
    return None


def expectedspecs_from_preset(label: str) -> ExpectedSpecs:
    if label not in RUNPOD_GPUS:
        raise ValueError(f"Unknown RunPod GPU '{label}'. Choose from: {', '.join(RUNPOD_GPUS)}")
    return ExpectedSpecs(gpu=1, gpu_type=label)


def _vmspec(label: str) -> VMSpec:
    g = RUNPOD_GPUS[label]
    return VMSpec(type=g["id"], cpu=g["cpu"], ram_gb=g["ram_gb"], gpus=1, price_per_hour=g["price"])


def resolve(request, location: str = None) -> VMSpec:
    """Map a sizing request to a concrete RunPod GPU.

    ExpectedSpecs.gpu_type ("a40"...) picks the GPU; default is the cheapest 48GB
    card. A VMSpec whose `type` is already a RunPod GPU id is trusted.
    """
    if isinstance(request, VMSpec) and request.type:
        g = _by_id(request.type)
        if g and not request.cpu:
            return _vmspec(next(k for k, v in RUNPOD_GPUS.items() if v["id"] == request.type))
        return request

    label = getattr(request, "gpu_type", None) or _DEFAULT_GPU
    if label not in RUNPOD_GPUS:
        raise RuntimeError(
            f"Unknown RunPod GPU '{label}'. Available: {', '.join(RUNPOD_GPUS)}."
        )
    return _vmspec(label)


def list_sizes(min_cpu: int = 0, min_ram_gb: float = 0,
               gpu: int = 0, gpu_type: str = None) -> list[dict]:
    out = []
    for label, g in RUNPOD_GPUS.items():
        if g["cpu"] < min_cpu or g["ram_gb"] < min_ram_gb:
            continue
        if gpu_type and gpu_type.lower() != label:
            continue
        out.append({"name": f"{label} ({g['id']})", "cpu": g["cpu"], "ram_gb": g["ram_gb"],
                    "gpus": 1, "price": g["price"]})
    out.sort(key=lambda s: s["price"])
    return out
