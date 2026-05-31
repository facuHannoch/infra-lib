from rich.console import Console

console = Console(highlight=False)


def step(msg: str):
    console.print(f"\n[bold cyan]▶[/bold cyan] {msg}")


def done(msg: str):
    console.print(f"[bold green]✓[/bold green] {msg}")


def warn(msg: str):
    console.print(f"[bold yellow]⚠[/bold yellow]  {msg}")
