from dataclasses import dataclass
from typing import Optional
from ._provision import provision
from ._transfer import transfer, run_command
from ._domain import Domain, BYODomain, CloudflareDomain, _default_caddyfile
from ._keys import ensure_key, key_path
from ._spec import VMSpec
from ._health import wait_for_url


@dataclass
class DeployResult:
    url: str
    ip: str


def deploy(
    source: str = None,
    name: str = "default",
    domain: Optional[Domain] = None,
    location: str = "CentralUS",
    ssh_key_path: str = None,
    install: str = None,
    vm: Optional[VMSpec] = None,
) -> DeployResult:
    ssh_key_path = ssh_key_path or ensure_key(name)
    outputs = provision(name=name, location=location, ssh_key_path=ssh_key_path, vm_spec=vm)
    ip = outputs["public_ip"]

    if domain:
        domain.provision_dns(ip)

    caddyfile = domain.caddyfile() if domain else (_default_caddyfile() if source else None)
    transfer(ip, source_dir=source, caddyfile=caddyfile, ssh_key_path=ssh_key_path)

    if install:
        run_command(ip, install, ssh_key_path=ssh_key_path)

    url = domain.url() if domain else (f"http://{ip}" if source else None)
    if url:
        wait_for_url(url)
    return DeployResult(url=url or ip, ip=ip)
