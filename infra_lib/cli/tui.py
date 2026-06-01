import sys

from .. import progress
from ..progress import console
from ..models import ExpectedSpecs, VMSpec
from ..providers import get_provider

# When resolution succeeds, proceed without a confirm step. Flip to False to
# always ask "Use this size?". (On failure the loop returns to the menu regardless.)
AUTO_CONFIRM = True


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _pos_int(t: str):
    return True if t.isdigit() and int(t) > 0 else "Enter a positive whole number"


def _pos_num(t: str):
    try:
        return True if float(t) > 0 else "Enter a positive number"
    except ValueError:
        return "Enter a positive number"


def _storage_ok(t: str):
    return True if t.isdigit() and int(t) >= 30 else "Enter a whole number of GB (min 30)"


def _pick_request(questionary, provider):
    """Step 1: preset / custom specs / exact type. Returns ExpectedSpecs|VMSpec, or None to abort."""
    size_term = provider.size_term
    choices = [
        questionary.Choice(
            title=f"{label:<8}  {p['cpu']} vCPU  {p['ram_gb']:>2}GB RAM   ~${p['price']:.3f}/hr",
            value=("preset", label),
        )
        for label, p in provider.presets.items()
    ]
    choices.append(questionary.Separator())
    choices.append(questionary.Choice(title="Custom specs…", value=("custom", None)))
    choices.append(questionary.Choice(title=f"Specify {size_term}…", value=("exact", None)))

    pick = questionary.select("VM size:", choices=choices, default=choices[1]).ask()
    if pick is None:
        return None
    kind, val = pick

    if kind == "preset":
        return provider.preset_specs(val)

    if kind == "custom":
        cpu = questionary.text("CPU (vCPUs):", default="2", validate=_pos_int).ask()
        if cpu is None:
            return None
        ram = questionary.text("RAM (GB):", default="8", validate=_pos_num).ask()
        if ram is None:
            return None
        return ExpectedSpecs(cpu=int(cpu), ram_gb=float(ram))

    # exact type
    sku = questionary.text(f"{size_term} (e.g. Standard_D2s_v3):").ask()
    if not sku or not sku.strip():
        return None
    return VMSpec(type=sku.strip())


def prompt_vm_spec(location: str, provider="azure"):
    """Interactive size + disk selection. Returns (ExpectedSpecs|VMSpec, storage_gb).

    `provider` is a Provider (or its name). Each choice is resolved against the
    provider (availability included), looping back to the menu if it can't be
    satisfied. Non-interactive: returns a default request for deploy() to resolve.
    """
    if isinstance(provider, str):
        provider = get_provider(provider)
    if not _is_interactive():
        return ExpectedSpecs(), 30
    try:
        import questionary
    except ImportError:
        return ExpectedSpecs(), 30

    chosen = None
    while chosen is None:
        request = _pick_request(questionary, provider)
        if request is None:
            print("Aborted.")
            sys.exit(0)
        try:
            vmspec = provider.resolve(request, location)
        except RuntimeError as e:
            progress.warn(str(e))
            continue
        console.print(f"[bold green]✓[/bold green] {vmspec}")
        if AUTO_CONFIRM or questionary.confirm("Use this size?", default=True).ask():
            chosen = vmspec

    storage = questionary.text("Disk size (GB):", default="30", validate=_storage_ok).ask()
    if storage is None:
        print("Aborted.")
        sys.exit(0)
    return chosen, int(storage)
