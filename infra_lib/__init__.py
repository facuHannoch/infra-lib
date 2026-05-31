from dataclasses import dataclass
from typing import Optional
from ._provision import provision
from ._transfer import transfer, run_command, run_setup
from ._domain import Domain, BYODomain, CloudflareDomain, _default_caddyfile
from ._keys import ensure_key, key_path
from ._spec import VMSpec
from ._health import wait_for_url
from ._progress import console, step, done


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
    ship: list[str] = None,
    setup: list[str] = None,
    vm: Optional[VMSpec] = None,
) -> DeployResult:
    ssh_key_path = ssh_key_path or ensure_key(name)
    outputs = provision(name=name, location=location, ssh_key_path=ssh_key_path, vm_spec=vm)
    ip = outputs["public_ip"]

    if domain:
        step(f"Provisioning DNS for [bold]{domain.name}[/bold]")
        domain.provision_dns(ip)
        done("DNS configured")

    caddyfile = domain.caddyfile() if domain else (_default_caddyfile() if (source or ship) else None)
    transfer(ip, source_dir=source, ship=ship, caddyfile=caddyfile, ssh_key_path=ssh_key_path)

    all_setup = list(setup or [])
    if install:
        all_setup.append(install)
    if all_setup:
        run_setup(ip, all_setup, ssh_key_path=ssh_key_path)

    url = domain.url() if domain else (f"http://{ip}" if (source or ship) else None)
    if url:
        wait_for_url(url)

    console.print(f"\n[bold green]Deployed![/bold green]  [cyan]{url or ip}[/cyan]  [dim](ssh azureuser@{ip})[/dim]")
    return DeployResult(url=url or ip, ip=ip)
