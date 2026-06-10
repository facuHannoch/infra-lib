import json
import logging
import os
import time
import urllib.request
import urllib.parse

from ...models import ExpectedSpecs, VMSpec
from ... import progress
from ...progress import warn

log = logging.getLogger(__name__)

_CACHE_DIR = os.path.expanduser("~/.infra-lib/cache")
_CACHE_TTL = 86400  # 24 hours

# Human term for an exact instance identifier on this provider (TUI label, etc).
SIZE_TERM = "SKU"

# Known-good Azure sizes used as fallback when pricing API is unavailable.
# Prices are approximate on-demand rates in USD/hr (CentralUS, as of 2024).
AZURE_PRESETS = {
    "micro":  {"sku": "Standard_B1s",    "cpu": 1, "ram_gb": 1,  "price": 0.011},
    "small":  {"sku": "Standard_D2s_v3", "cpu": 2, "ram_gb": 8,  "price": 0.096},
    "medium": {"sku": "Standard_D4s_v3", "cpu": 4, "ram_gb": 16, "price": 0.192},
    "large":  {"sku": "Standard_D8s_v3", "cpu": 8, "ram_gb": 32, "price": 0.384},
}

_FALLBACK_PRICES = {v["sku"]: v["price"] for v in AZURE_PRESETS.values()}

# Friendly GPU name -> the token Azure puts in the SKU name. Matched delimited
# (f"_{token}_") so "a10" doesn't also match A100 SKUs. None gpu_type = any GPU.
GPU_TOKENS = {
    "t4":   "T4",
    "a10":  "A10",
    "a100": "A100",
    "h100": "H100",
}


def _gpu_token(gpu_type: str) -> str:
    return f"_{GPU_TOKENS.get(gpu_type.lower(), gpu_type.upper())}_"


def expectedspecs_from_preset(label: str) -> ExpectedSpecs:
    """Turn a preset label ('small') into the cpu/ram minimums it implies."""
    preset = AZURE_PRESETS.get(label)
    if not preset:
        raise ValueError(f"Unknown vm preset '{label}'. Choose from: {', '.join(AZURE_PRESETS)}")
    return ExpectedSpecs(cpu=preset["cpu"], ram_gb=preset["ram_gb"])


def _cache_path(location: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"prices_{location.lower()}.json")


def _azure_list_sizes(location: str) -> dict:
    """Fetch available VM sizes and prices in a region from Azure Retail Prices API."""
    cache_file = _cache_path(location)
    if os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < _CACHE_TTL:
            with open(cache_file) as f:
                return json.load(f)

    filter_str = (
        f"serviceName eq 'Virtual Machines' and "
        f"armRegionName eq '{location.lower()}' and "
        f"priceType eq 'Consumption'"
    )
    encoded_filter = urllib.parse.quote(filter_str)
    base_url = f"https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&$filter={encoded_filter}&$top=1000"

    try:
        sizes = {}
        skip = 0
        while True:
            url = f"{base_url}&$skip={skip}"
            with urllib.request.urlopen(url) as resp:
                data = json.loads(resp.read())
            items = data.get("Items", [])
            if not items:
                break
            for item in items:
                name = item.get("armSkuName", "")
                sku = item.get("skuName", "")
                if not name or "Spot" in sku or "Low Priority" in sku:
                    continue
                if name not in sizes:
                    sizes[name] = item.get("retailPrice", 0)
            if len(items) < 1000:
                break
            skip += 1000
        with open(cache_file, "w") as f:
            json.dump(sizes, f)
        return sizes
    except Exception as e:
        log.debug("Azure pricing API request failed: %s", e, exc_info=True)
        if os.path.exists(cache_file):
            warn("Azure pricing API unavailable, using cached prices.")
            with open(cache_file) as f:
                return json.load(f)
        warn("Azure pricing API unavailable, using built-in fallback prices.")
        return _FALLBACK_PRICES


def _preset_specs() -> list[dict]:
    """Built-in size specs used when the Azure SKU API is unavailable."""
    return [
        {"name": p["sku"], "cpu": p["cpu"], "ram_gb": float(p["ram_gb"]), "gpus": 0}
        for p in AZURE_PRESETS.values()
    ]


def _azure_size_specs(location: str, credential) -> list[dict]:
    """Fetch VM size specs (CPU, RAM, architecture) from Azure resource SKUs API.

    Falls back to the built-in preset specs if the API is unavailable, so a
    SKU-API outage degrades gracefully instead of killing provisioning (mirrors
    the pricing fallback in _azure_list_sizes).
    """
    from azure.mgmt.compute import ComputeManagementClient

    subscription_id = os.environ.get("ARM_SUBSCRIPTION_ID", "")
    client = ComputeManagementClient(credential, subscription_id)

    try:
        result = []
        for sku in client.resource_skus.list(filter=f"location eq '{location}'"):
            if sku.resource_type != "virtualMachines":
                continue
            # sku.restrictions is non-empty whenever Azure has capacity or zone restrictions on that size.
            if sku.restrictions:
                continue
            caps = {c.name: c.value for c in (sku.capabilities or [])}
            if caps.get("CpuArchitectureType", "x64") != "x64":
                continue
            cpu = int(caps.get("vCPUs", 0))
            ram_gb = float(caps.get("MemoryGB", 0))
            gpus = int(caps.get("GPUs", 0))
            if cpu and ram_gb:
                result.append({"name": sku.name, "cpu": cpu, "ram_gb": ram_gb, "gpus": gpus})
        return result
    except Exception as e:
        log.debug("Azure SKU API request failed: %s", e, exc_info=True)
        warn("Azure SKU API unavailable, using built-in fallback sizes.")
        return _preset_specs()


def resolve(request, location: str) -> VMSpec:
    """Turn a sizing request into a concrete, available VMSpec.

    `request` is either an ExpectedSpecs (cpu/ram minimums -> cheapest satisfying
    SKU) or a VMSpec naming an exact `type` (validated + filled in). Raises
    RuntimeError if nothing satisfies it or the named type isn't available here.
    Idempotent: a fully-resolved VMSpec is returned unchanged.
    """
    from .auth import load_azure_credentials
    from azure.identity import ClientSecretCredential

    # Already concrete (e.g. resolved earlier in the TUI) — trust it.
    if isinstance(request, VMSpec) and request.cpu and request.ram_gb:
        return request

    load_azure_credentials()
    credential = ClientSecretCredential(
        tenant_id=os.environ["ARM_TENANT_ID"],
        client_id=os.environ["ARM_CLIENT_ID"],
        client_secret=os.environ["ARM_CLIENT_SECRET"],
    )

    with progress.status(f"Checking availability in {location}..."):
        specs = _azure_size_specs(location, credential)
        prices = _azure_list_sizes(location)

        if isinstance(request, VMSpec):
            # Exact type requested: validate it exists/is available, fill specs+price.
            match = next((s for s in specs if s["name"] == request.type), None)
            if match is None or request.type not in prices:
                raise RuntimeError(f"{request.type} isn't available in {location}.")
            best = match
        else:
            want_gpu = request.gpu or request.gpu_type
            candidates = [
                s for s in specs
                if s["cpu"] >= request.cpu and s["ram_gb"] >= request.ram_gb and s["name"] in prices
            ]
            if want_gpu:
                need = max(request.gpu, 1)
                candidates = [s for s in candidates if s.get("gpus", 0) >= need]
                if request.gpu_type:
                    token = _gpu_token(request.gpu_type)
                    candidates = [s for s in candidates if token in s["name"]]
            else:
                # A non-GPU request should never land on (pricey) GPU hardware.
                candidates = [s for s in candidates if not s.get("gpus", 0)]
            if not candidates:
                raise RuntimeError(_no_match_message(request, location))
            candidates.sort(key=lambda s: (prices.get(s["name"], 9999), s["cpu"], s["ram_gb"]))
            best = candidates[0]

    return VMSpec(
        type=best["name"],
        cpu=best["cpu"],
        ram_gb=best["ram_gb"],
        gpus=best.get("gpus", 0),
        price_per_hour=prices.get(best["name"]),
    )


def _no_match_message(request, location: str) -> str:
    if request.gpu or request.gpu_type:
        what = f"{request.gpu or 1}x {request.gpu_type or 'GPU'}"
        return (
            f"No GPU size in {location} matches {what}. The family may be "
            f"restricted here or you may have no quota for it — try another region "
            f"or request quota: https://portal.azure.com/#view/Microsoft.Azure.Capacity/QuotaMenuBlade/~/myQuotas"
        )
    return f"No size in {location} matches {request.cpu} vCPU / {request.ram_gb}GB RAM."
