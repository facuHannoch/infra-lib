import sys
from ..providers.azure.sizes import AZURE_PRESETS


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def prompt_vm_spec(provider: str = "azure") -> str:
    """Interactively prompt the user to select a VM size. Returns a preset label."""
    if not _is_interactive():
        return "small"

    try:
        import questionary
    except ImportError:
        return "small"

    choices = [
        questionary.Choice(
            title=f"{label:<8}  {p['cpu']} vCPU  {p['ram_gb']:>2}GB RAM   ~${p['price']:.3f}/hr",
            value=label,
        )
        for label, p in AZURE_PRESETS.items()
    ]

    label = questionary.select(
        "VM size:",
        choices=choices,
        default=choices[1],  # small
    ).ask()

    if label is None:
        print("Aborted.")
        sys.exit(0)

    return label
