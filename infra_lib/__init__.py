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
    port: int = None,
) -> DeployResult:
    import sys
    ssh_key_path = ssh_key_path or ensure_key(name)
    outputs = provision(name=name, location=location, ssh_key_path=ssh_key_path, vm_spec=vm)
    ip = outputs["public_ip"]

    console.print(f"\n  [dim]IP:[/dim]  [bold cyan]{ip}[/bold cyan]  [dim](ssh azureuser@{ip})[/dim]")

    if domain:
        if hasattr(domain, "api_token"):
            # CloudflareDomain: auto-provision DNS
            step(f"Provisioning DNS for [bold]{domain.name}[/bold]")
            domain.provision_dns(ip)
            done("DNS configured")
        else:
            # BYODomain: user must point DNS manually
            console.print(
                f"\n  Point [bold]{domain.name}[/bold] → [cyan]{ip}[/cyan] at your DNS provider."
            )
            try:
                import questionary
                questionary.press_any_key_to_continue("  Press Enter once DNS is configured...").ask()
            except ImportError:
                input("  Press Enter once DNS is configured...")

    has_content = source or ship or port
    caddyfile = domain.caddyfile(port=port) if domain else (_default_caddyfile(port=port) if has_content else None)
    transfer(ip, source_dir=source, ship=ship, caddyfile=caddyfile, ssh_key_path=ssh_key_path)

    all_setup = list(setup or [])
    if install:
        all_setup.append(install)
    if all_setup:
        run_setup(ip, all_setup, ssh_key_path=ssh_key_path)

    url = domain.url() if domain else (f"http://{ip}" if has_content else None)

    if url and sys.stdin.isatty():
        try:
            import questionary
            test = questionary.confirm("Test connection?", default=True).ask()
        except ImportError:
            test = input("Test connection? [Y/n] ").strip().lower() not in ("n", "no")
        if test:
            wait_for_url(url)
    elif url:
        wait_for_url(url)

    console.print(f"\n[bold green]Deployed![/bold green]  [cyan]{url or ip}[/cyan]")
    return DeployResult(url=url or ip, ip=ip)
