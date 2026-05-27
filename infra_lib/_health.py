import time
import urllib.request
import urllib.error
import ssl


def wait_for_url(url: str, timeout: int = 300, interval: int = 5):
    ctx = ssl.create_default_context()
    deadline = time.time() + timeout
    last_error = None

    print(f"Waiting for {url} to become available...", flush=True)
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "infra-lib/healthcheck"})
            with urllib.request.urlopen(req, timeout=5, context=ctx):
                print(f"  {url} is up.")
                return
        except Exception as e:
            last_error = e
            time.sleep(interval)

    raise TimeoutError(f"{url} did not become available after {timeout}s. Last error: {last_error}")
