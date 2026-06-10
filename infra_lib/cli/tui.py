import os
import sys

from .. import progress
from ..progress import console
from ..models import ExpectedSpecs, VMSpec, Unit, ShipItem
from ..core.domain import build_domain
from ..providers import get_provider, provider_names

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
    return True if t.isdigit() and int(t) > 0 else "Enter a whole number of GB"


def _nonempty(t: str):
    return True if t and t.strip() else "Required"


def _pick_provider(questionary) -> str | None:
    """Step 1: choose a provider; warn (but continue) if not authenticated."""
    name = questionary.select(
        "Provider:", choices=list(provider_names())).ask()
    if name is None:
        return None
    try:
        get_provider(name).load_credentials()
    except Exception:
        progress.warn(f"Not authenticated with '{name}'. Run `infra-lib auth {name}` "
                      f"before deploying (continuing for now).")
    return name


def _pick_request(questionary, provider):
    """Step 2.2: preset / custom specs / exact id, generalized per provider.

    Returns ExpectedSpecs|VMSpec, or None to abort. For GPU-first providers the
    presets are the GPUs and there's no cpu/ram 'custom' option.
    """
    size_term = provider.size_term
    word = size_term if provider.gpu_first else "size"
    choices = [
        questionary.Choice(
            title=f"{label:<12} {p['cpu']} vCPU  {p['ram_gb']:>3}GB RAM   ~${p['price']:.3f}/hr",
            value=("preset", label),
        )
        for label, p in provider.presets.items()
    ]
    choices.append(questionary.Separator())
    if not provider.gpu_first:
        choices.append(questionary.Choice(title="Custom specs…", value=("custom", None)))
    choices.append(questionary.Choice(title=f"Specify {size_term}…", value=("exact", None)))

    pick = questionary.select(f"{word.capitalize()}:", choices=choices, default=choices[0]).ask()
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

    sku = questionary.text(f"{size_term} (e.g. {'NVIDIA A40' if provider.gpu_first else 'Standard_D2s_v3'}):").ask()
    if not sku or not sku.strip():
        return None
    return VMSpec(type=sku.strip())


def _resolve_loop(questionary, provider, location):
    """Pick a size and resolve it against the provider, looping on failure."""
    while True:
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
            return vmspec


def prompt_unit(location: str, source: str = None):
    """Interactive single-unit builder. Returns (provider_name, Unit) or None.

    Flow: provider -> (type, from the provider) -> size + storage -> port ->
    type-specific (vm: ship the source dir + optional domain; pod: image).
    None means the TUI isn't available (no tty / questionary); callers fall back
    to defaults. Aborts the process on an explicit cancel.
    """
    if not _is_interactive():
        return None
    try:
        import questionary
    except ImportError:
        return None

    provider_name = _pick_provider(questionary)
    if provider_name is None:
        print("Aborted.")
        sys.exit(0)
    provider = get_provider(provider_name)
    console.print(f"  type: [bold]{provider.unit_type}[/bold]")

    hardware = _resolve_loop(questionary, provider, location)

    storage = questionary.text("Disk/volume size (GB):", default="30", validate=_storage_ok).ask()
    if storage is None:
        print("Aborted.")
        sys.exit(0)

    port_raw = questionary.text("Port to expose (blank = none):", default="").ask()
    ports = [int(port_raw)] if port_raw and port_raw.strip().isdigit() else []

    unit = Unit(type=provider.unit_type, hardware=hardware, ports=ports)
    unit.disk.size_gb = int(storage)

    if provider.unit_type == "pod":
        image = questionary.text("Image (e.g. ghcr.io/me/app:latest):", validate=_nonempty).ask()
        if image is None:
            print("Aborted.")
            sys.exit(0)
        unit.image = image.strip()
    else:
        if source:
            unit.ship.append(ShipItem(src=os.path.abspath(source)))
        domain = questionary.text("Domain (blank = none):", default="").ask()
        if domain and domain.strip():
            unit.domain = build_domain(name=domain.strip(), strategy="own",
                                       proxied=False, cloudflare_token=None)

    return provider_name, unit
