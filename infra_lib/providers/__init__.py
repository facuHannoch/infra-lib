"""Provider registry.

`get_provider(name)` is the single entry point the rest of the library uses to
reach a cloud. Provider modules are imported lazily so that importing infra_lib
(or using a different provider) doesn't pull in every cloud's SDK.
"""
from .base import Provider

_BUILTIN = {
    "azure": ("infra_lib.providers.azure.provider", "AzureProvider"),
    "runpod": ("infra_lib.providers.runpod.provider", "RunPodProvider"),
}
_cache: dict[str, Provider] = {}


def provider_names() -> list[str]:
    return list(_BUILTIN)


def get_provider(name: str = "azure") -> Provider:
    name = (name or "azure").lower()
    if name not in _cache:
        if name not in _BUILTIN:
            raise ValueError(
                f"Unknown provider '{name}'. Available: {', '.join(_BUILTIN)}"
            )
        import importlib
        module_path, class_name = _BUILTIN[name]
        cls = getattr(importlib.import_module(module_path), class_name)
        _cache[name] = cls()
    return _cache[name]
