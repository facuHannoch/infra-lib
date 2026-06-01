import logging
import sys
from ..progress import Reporter, console

log = logging.getLogger(__name__)


class ConsoleReporter(Reporter):
    """Interactive reporter: rich spinners + questionary prompts."""

    def step(self, msg: str):
        console.print(f"\n[bold cyan]▶[/bold cyan] {msg}")

    def done(self, msg: str):
        console.print(f"[bold green]✓[/bold green] {msg}")

    def warn(self, msg: str):
        console.print(f"[bold yellow]⚠[/bold yellow]  {msg}")

    def raw(self, text: str):
        console.print(text, markup=False, highlight=False, style="dim")

    def status(self, msg: str):
        return console.status(f"[bold]{msg}", spinner="dots")

    def show_ip(self, ip: str):
        console.print(f"\n  [dim]IP:[/dim]  [bold cyan]{ip}[/bold cyan]  [dim](ssh azureuser@{ip})[/dim]")

    def need_dns(self, domain, ip: str):
        console.print(f"\n  Point [bold]{domain.name}[/bold] → [cyan]{ip}[/cyan] at your DNS provider.")
        # Can't pause without a terminal (piped/CI/background) — proceed and let
        # the URL check poll for DNS, rather than crashing on EOF.
        if not sys.stdin.isatty():
            console.print("  [dim](non-interactive: continuing without waiting for DNS)[/dim]")
            return
        try:
            import questionary
            questionary.press_any_key_to_continue("  Press Enter once DNS is configured...").ask()
        except ImportError:
            input("  Press Enter once DNS is configured...")
        except EOFError as e:
            log.debug("DNS pause prompt failed: %s", e, exc_info=True)
            self.warn("Couldn't pause for DNS confirmation; continuing.")

    def confirm_test(self) -> bool:
        if not sys.stdin.isatty():
            return True
        try:
            import questionary
            return questionary.confirm("Test connection?", default=True).ask()
        except ImportError:
            return input("Test connection? [Y/n] ").strip().lower() not in ("n", "no")

    def finished(self, deployment):
        target = deployment.url or deployment.ip
        console.print(f"\n[bold green]Deployed![/bold green]  [cyan]{target}[/cyan]")
