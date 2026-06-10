"""A tiny local record of what was deployed where.

The moment there's more than one provider, management ops (get/list/down/pause)
need to know whether a deployment is an Azure stack or a RunPod pod — the name
alone doesn't say. Each deploy writes one JSON file here; the ops read it back to
route to the right provider.

Deliberately minimal: a directory of `<name>.json`, no database. A deployment
not found here falls back to the default provider (covers boxes created before
this registry existed).
"""
import json
import os

_DIR = os.path.expanduser("~/.infra-lib/deployments")


def _path(name: str) -> str:
    return os.path.join(_DIR, f"{name}.json")


def record(name: str, provider: str, handle: str = None, **extra) -> None:
    """Remember that `name` was deployed to `provider`. `handle` is the
    provider's own id for it (Pulumi stack name, RunPod pod id)."""
    os.makedirs(_DIR, exist_ok=True)
    entry = {"name": name, "provider": provider, "handle": handle or name, **extra}
    with open(_path(name), "w") as f:
        json.dump(entry, f, indent=2)


def get(name: str) -> dict | None:
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def provider_of(name: str, default: str = "azure") -> str:
    entry = get(name)
    return entry["provider"] if entry else default


def all() -> list[dict]:
    if not os.path.isdir(_DIR):
        return []
    out = []
    for fn in os.listdir(_DIR):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(_DIR, fn)) as f:
                    out.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def remove(name: str) -> None:
    try:
        os.remove(_path(name))
    except FileNotFoundError:
        pass
