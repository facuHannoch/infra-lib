import sys
from ._spec import VMSpec
from ._resolve import AZURE_PRESETS


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def prompt_vm_spec(provider: str = "azure") -> VMSpec:
    """Interactively prompt the user to select a VM size. Returns a VMSpec."""
    if not _is_interactive():
        return VMSpec()

    try:
        import questionary
    except ImportError:
        return VMSpec()

    presets = AZURE_PRESETS

    choices = [
        questionary.Choice(
            title=f"{label:<8}  {p['cpu']} vCPU  {p['ram_gb']:>2}GB RAM   ~${p['price']:.3f}/hr",
            value=label,
        )
        for label, p in presets.items()
    ]

    label = questionary.select(
        "VM size:",
        choices=choices,
        default=choices[1],  # small
    ).ask()

    if label is None:
        print("Aborted.")
        sys.exit(0)

    storage = questionary.text(
        "Storage (GB):",
        default="30",
        validate=lambda v: v.isdigit() and int(v) >= 10 or "Enter a number >= 10",
    ).ask()

    if storage is None:
        print("Aborted.")
        sys.exit(0)

    p = presets[label]
    return VMSpec(cpu=p["cpu"], ram_gb=p["ram_gb"], storage_gb=int(storage))
