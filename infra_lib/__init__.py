from dataclasses import dataclass
from typing import Optional
from ._provision import provision
from ._transfer import transfer
from ._domain import Domain, BYODomain, CloudflareDomain, _default_caddyfile


@dataclass
class DeployResult:
    url: str
    ip: str


def deploy(
    source: str,
    name: str = "default",
    domain: Optional[Domain] = None,
    location: str = "CentralUS",
    ssh_key_path: str = None,
) -> DeployResult:
    outputs = provision(name=name, location=location, ssh_key_path=ssh_key_path)
    ip = outputs["public_ip"]

    if domain:
        domain.provision_dns(ip)

    caddyfile = domain.caddyfile() if domain else _default_caddyfile()
    transfer(ip, source, caddyfile=caddyfile, ssh_key_path=ssh_key_path)

    url = domain.url() if domain else f"http://{ip}"
    return DeployResult(url=url, ip=ip)
