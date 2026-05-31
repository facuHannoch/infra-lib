import json
import os
import time
import urllib.request
import urllib.parse
from ._spec import VMSpec, ResolvedSize

_CACHE_DIR = os.path.expanduser("~/.infra-lib/cache")
_CACHE_TTL = 86400  # 24 hours

# Known-good Azure sizes used as fallback when pricing API is unavailable.
# Prices are approximate on-demand rates in USD/hr (CentralUS, as of 2024).
AZURE_PRESETS = {
    "micro":  {"sku": "Standard_B1s",   "cpu": 1, "ram_gb": 1,  "price": 0.011},
    "small":  {"sku": "Standard_D2s_v3", "cpu": 2, "ram_gb": 8,  "price": 0.096},
    "medium": {"sku": "Standard_D4s_v3", "cpu": 4, "ram_gb": 16, "price": 0.192},
    "large":  {"sku": "Standard_D8s_v3", "cpu": 8, "ram_gb": 32, "price": 0.384},
}

_FALLBACK_PRICES = {v["sku"]: v["price"] for v in AZURE_PRESETS.values()}


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
    except Exception:
        if os.path.exists(cache_file):
            from ._progress import warn
            warn("Azure pricing API unavailable, using cached prices.")
            with open(cache_file) as f:
                return json.load(f)
        from ._progress import warn
        warn("Azure pricing API unavailable, using built-in fallback prices.")
        return _FALLBACK_PRICES


def _azure_size_specs(location: str, credential) -> list[dict]:
    """Fetch VM size specs (CPU, RAM, architecture) from Azure resource SKUs API."""
    from azure.mgmt.compute import ComputeManagementClient
    import os

    subscription_id = os.environ.get("ARM_SUBSCRIPTION_ID", "")
    client = ComputeManagementClient(credential, subscription_id)

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
        if cpu and ram_gb:
            result.append({"name": sku.name, "cpu": cpu, "ram_gb": ram_gb})
    return result


def resolve_azure_size(spec: VMSpec, location: str) -> ResolvedSize:
    from azure.identity import ClientSecretCredential
    import os

    credential = ClientSecretCredential(
        tenant_id=os.environ["ARM_TENANT_ID"],
        client_id=os.environ["ARM_CLIENT_ID"],
        client_secret=os.environ["ARM_CLIENT_SECRET"],
    )

    specs = _azure_size_specs(location, credential)
    prices = _azure_list_sizes(location)

    candidates = [
        s for s in specs
        if s["cpu"] >= spec.cpu
        and s["ram_gb"] >= spec.ram_gb
        and s["name"] in prices
    ]

    if not candidates:
        raise RuntimeError(
            f"No VM size found in {location} with >= {spec.cpu} vCPU and >= {spec.ram_gb}GB RAM."
        )

    candidates.sort(key=lambda s: (prices.get(s["name"], 9999), s["cpu"], s["ram_gb"]))
    best = candidates[0]

    return ResolvedSize(
        name=best["name"],
        cpu=best["cpu"],
        ram_gb=best["ram_gb"],
        price_per_hour=prices.get(best["name"]),
    )
