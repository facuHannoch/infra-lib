"""RunPod auth: a single API key.

Stored in the same ~/.infra-lib/credentials file as other providers (its own
[runpod] section), or supplied via the RUNPOD_API_KEY env var (CI use).
"""
import configparser
import os

_CREDENTIALS_FILE = os.path.expanduser("~/.infra-lib/credentials")


def _save_key(api_key: str):
    config = configparser.ConfigParser()
    config.read(_CREDENTIALS_FILE)
    config["runpod"] = {"api_key": api_key}
    os.makedirs(os.path.dirname(_CREDENTIALS_FILE), exist_ok=True)
    with open(_CREDENTIALS_FILE, "w") as f:
        config.write(f)
    os.chmod(_CREDENTIALS_FILE, 0o600)


def _read_key() -> str | None:
    if os.environ.get("RUNPOD_API_KEY"):
        return os.environ["RUNPOD_API_KEY"]
    config = configparser.ConfigParser()
    config.read(_CREDENTIALS_FILE)
    if "runpod" in config and config["runpod"].get("api_key"):
        return config["runpod"]["api_key"]
    return None


def load_runpod_key() -> str:
    """Return the API key, configuring the runpod SDK, or raise if unset."""
    key = _read_key()
    if not key:
        raise RuntimeError(
            "No RunPod API key found. Run 'infra-lib auth runpod --api-key <KEY>' "
            "or set RUNPOD_API_KEY. Get a key at https://www.runpod.io/console/user/settings"
        )
    import runpod
    runpod.api_key = key
    return key


def auth_runpod():
    """Interactive: prompt for an API key and save it."""
    key = input("RunPod API key: ").strip()
    if not key:
        raise RuntimeError("No API key entered.")
    _save_key(key)
    print(f"RunPod credentials saved to {_CREDENTIALS_FILE}")


def save_runpod_key(api_key: str = None, **_) -> str:
    """Non-interactive: persist a provided API key. Returns the path."""
    if not api_key:
        raise ValueError("missing required credential: api_key")
    _save_key(api_key)
    return _CREDENTIALS_FILE
