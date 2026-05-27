import os
import subprocess

_KEYS_DIR = os.path.expanduser("~/.infra-lib/keys")


def key_path(name: str) -> str:
    return os.path.join(_KEYS_DIR, f"{name}_id_rsa")


def ensure_key(name: str) -> str:
    path = key_path(name)
    if not os.path.exists(path):
        os.makedirs(_KEYS_DIR, mode=0o700, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", path, "-N", ""],
            check=True,
            capture_output=True,
        )
    return path
