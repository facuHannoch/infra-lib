import time
import urllib.request
import ssl
from ._progress import console, done


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
