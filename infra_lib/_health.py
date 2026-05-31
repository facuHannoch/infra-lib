import time
import urllib.request
import ssl
from ._progress import console, done, warn


def check_port(host: str, port: int, ssh_key_path: str) -> bool:
    """Returns True if the given port is listening on the VM."""
    try:
        from ._transfer import _connect
        client = _connect(host, ssh_key_path)
        _, stdout, _ = client.exec_command(f"ss -tlnp | grep ':{port} '")
        output = stdout.read().decode().strip()
        client.close()
        return bool(output)
    except Exception:
        return False


def wait_for_port(host: str, port: int, ssh_key_path: str, timeout: int = 60) -> bool:
    """Polls until the port is listening on the VM. Returns True if it comes up."""
    deadline = time.time() + timeout
    with console.status(f"[bold]Waiting for app to start on port {port}...", spinner="dots"):
        while time.time() < deadline:
            if check_port(host, port, ssh_key_path):
                done(f"App is listening on port {port}")
                return True
            time.sleep(3)
    return False


def wait_for_url(url: str, timeout: int = 300, interval: int = 5):
    ctx = ssl.create_default_context()
    deadline = time.time() + timeout
    last_error = None

    with console.status(f"[bold]Waiting for [cyan]{url}[/cyan] to come online...", spinner="dots"):
        while time.time() < deadline:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "infra-lib/healthcheck"})
                with urllib.request.urlopen(req, timeout=5, context=ctx):
                    done(f"[cyan]{url}[/cyan] is live")
                    return
            except Exception as e:
                last_error = e
                time.sleep(interval)

    raise TimeoutError(f"{url} did not become available after {timeout}s. Last error: {last_error}")
