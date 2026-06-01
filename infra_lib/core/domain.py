from dataclasses import dataclass
from typing import Optional
import re

_DOMAIN_RE = re.compile(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


def _validate_domain(name: str):
    if not _DOMAIN_RE.match(name):
        raise ValueError(f"Invalid domain name: {name!r}")


def build_domain(name: str = None, strategy: str = None, proxied: bool = False,
                 cloudflare_token: str = None) -> Optional["Domain"]:
    """Construct the right Domain from raw inputs (config or CLI flags).

    strategy None/"http" -> no managed domain (plain http://<ip>).
    A bare domain with no strategy defaults to "own".
    """
    strategy = strategy or ("own" if name else None)
    if strategy in (None, "http"):
        return None
    if not name:
        raise ValueError(f"a domain name is required for strategy '{strategy}'")
    if strategy == "own":
        return BYODomain(name=name, proxied=proxied)
    if strategy == "cloudflare":
        if not cloudflare_token:
            raise ValueError(
                "cloudflare strategy needs an API token (--cloudflare-token or CLOUDFLARE_API_TOKEN)"
            )
        return CloudflareDomain(name=name, api_token=cloudflare_token, proxied=proxied)
    raise ValueError(f"unknown domain strategy: {strategy!r}")


def default_caddyfile(port: int = None) -> str:
    if port:
        return f":80 {{\n    reverse_proxy localhost:{port}\n}}\n"
    return ":80 {\n    root * /srv/files\n    file_server browse\n}\n"


class Domain:
    #: whether provision_dns() wires DNS automatically (vs. user pointing it manually)
    auto_dns = False

    def caddyfile_host(self) -> str:
        raise NotImplementedError

    def caddyfile(self, port: int = None) -> str:
        if port:
            return f"{self.caddyfile_host()} {{\n    reverse_proxy localhost:{port}\n}}\n"
        return f"{self.caddyfile_host()} {{\n    root * /srv/files\n    file_server browse\n}}\n"

    def provision_dns(self, ip: str):
        pass

    def url(self) -> str:
        host = self.caddyfile_host()
        if host.startswith("http://"):
            return f"http://{self.name}"
        return f"https://{self.name}"


@dataclass
class BYODomain(Domain):
    """User already owns the domain and will point DNS at the VM IP."""
    name: str
    proxied: bool = False
    auto_dns = False

    def __post_init__(self):
        _validate_domain(self.name)

    def caddyfile_host(self) -> str:
        return f"http://{self.name}" if self.proxied else self.name


@dataclass
class CloudflareDomain(Domain):
    """Wires DNS automatically via the Cloudflare API."""
    name: str
    api_token: str
    zone_id: Optional[str] = None
    proxied: bool = False
    auto_dns = True

    def __post_init__(self):
        _validate_domain(self.name)

    def caddyfile_host(self) -> str:
        return f"http://{self.name}" if self.proxied else self.name

    def provision_dns(self, ip: str):
        import urllib.request
        import urllib.error
        import json

        zone_id = self.zone_id or self._get_zone_id()
        payload = json.dumps({
            "type": "A",
            "name": self.name,
            "content": ip,
            "ttl": 60,
            "proxied": self.proxied,
        }).encode()

        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read())
                if not result.get("success"):
                    raise RuntimeError(f"Cloudflare DNS error: {result.get('errors')}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Cloudflare API error: {e.read().decode()}")

    def _get_zone_id(self) -> str:
        import urllib.request
        import json

        parts = self.name.split(".")
        zone_name = ".".join(parts[-2:])
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/zones?name={zone_name}",
            headers={"Authorization": f"Bearer {self.api_token}"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        zones = result.get("result", [])
        if not zones:
            raise RuntimeError(f"No Cloudflare zone found for {zone_name}")
        return zones[0]["id"]
