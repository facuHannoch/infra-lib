from dataclasses import dataclass, field
from typing import Optional


def _default_caddyfile() -> str:
    return ":80 {\n    root * /srv/files\n    file_server browse\n}\n"


class Domain:
    def caddyfile_host(self) -> str:
        raise NotImplementedError

    def caddyfile(self) -> str:
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
    """User already owns the domain and has pointed DNS at the VM IP."""
    name: str
    proxied: bool = False

    def caddyfile_host(self) -> str:
        return f"http://{self.name}" if self.proxied else self.name


@dataclass
class CloudflareDomain(Domain):
    """Wires DNS automatically via Cloudflare API."""
    name: str
    api_token: str
    zone_id: Optional[str] = None
    proxied: bool = False

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
