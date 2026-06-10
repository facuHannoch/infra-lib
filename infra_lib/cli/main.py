import argparse
import logging
import os
import sys

from .. import progress
from ..pipeline import deploy, list_deployments, destroy
from ..models import Infrastructure, ExpectedSpecs, VMSpec, ShipItem
from ..core.domain import build_domain
from ..providers import get_provider, provider_names
from ..config import load_config, default_provider_for_type
from .tui import prompt_unit
from .reporter import ConsoleReporter

# Presets of the default provider, used for --vm help/choices at parse time.
_DEFAULT_PRESETS = list(get_provider().presets)


def _template_path() -> str:
    # templates live at the package root, one level up from cli/
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "infra.yml")


def _load_template() -> str:
    with open(_template_path()) as f:
        return f.read()


def _open_editor_config(name: str) -> str:
    import tempfile, subprocess
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
        f.write(_load_template().format(name=name))
        tmp = f.name
    subprocess.call([editor, tmp])
    with open(tmp) as f:
        content = f.read()
    os.unlink(tmp)
    return content


def _resolve_config(args):
    """Return the base Infrastructure from config (or None), exiting on error."""
    try:
        if args.no_config:
            return None
        if args.config is None:
            return load_config()
        if args.config == "":
            import tempfile
            name_hint = args.name if args.name != "default" else "myapp"
            content = _open_editor_config(name_hint)
            with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
                f.write(content)
                tmp = f.name
            try:
                return load_config(tmp)
            finally:
                os.unlink(tmp)
        cfg = load_config(args.config)
        if cfg is None:
            print(f"error: config file not found: {args.config}")
            sys.exit(1)
        return cfg
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)


def _parse_gpu_arg(s: str):
    """--gpu accepts a count ('2') or a type name ('a100'). Returns (count, type)."""
    s = s.strip()
    if s.isdigit():
        return int(s), None
    return 1, s.lower()


def _has_unit_flags(args) -> bool:
    """Whether the user specified the unit on the CLI (so we skip the TUI)."""
    return any([args.type, args.provider, args.size, args.cpu, args.ram,
                args.instance_type, args.gpu, args.image, args.build])


def _apply_overlays(args, infra):
    """Apply flags that layer onto any unit, from either the TUI or config path."""
    unit = infra.units[0]
    if args.name != "default":
        infra.name = args.name
    if args.location != "CentralUS":
        infra.location = args.location
    if args.storage:
        unit.disk.size_gb = args.storage
    if args.install:
        unit.setup.append(args.install)
    if args.start:
        unit.start = args.start
    if args.port:
        unit.ports = [args.port]
    if args.domain or args.domain_strategy:
        try:
            unit.domain = build_domain(
                name=args.domain, strategy=args.domain_strategy,
                proxied=args.proxied, cloudflare_token=args.cloudflare_token,
            )
        except ValueError as e:
            print(f"error: {e}")
            sys.exit(1)


def cmd_deploy(args):
    infra = _resolve_config(args)
    had_config = infra is not None

    # No config and no unit-defining flags: drive the interactive builder
    # (provider -> type -> size -> port -> type-specific). Falls through to the
    # default path when there's no tty / questionary.
    if not had_config and not _has_unit_flags(args):
        picked = prompt_unit(args.location, args.source)
        if picked is not None:
            provider_name, unit = picked
            infra = Infrastructure(location=args.location, provider=provider_name, units=[unit])
            _apply_overlays(args, infra)
            deploy(infra, ssh_key_path=args.ssh_key)
            return

    infra = infra or Infrastructure()
    unit = infra.units[0]

    # Type / provider. `image`/`build` imply a pod; `--type`/`--provider` override.
    # Setting type (explicitly or implied) without a provider re-derives it.
    unit_type = args.type or (("pod" if (args.image or args.build) else None))
    if unit_type:
        unit.type = unit_type
    if args.provider:
        infra.provider = args.provider
    elif unit_type:
        infra.provider = default_provider_for_type(unit_type)
    provider = get_provider(infra.provider)

    if args.source:
        if not os.path.isdir(args.source):
            print(f"error: source must be a directory: {args.source}")
            sys.exit(1)
        unit.ship.append(ShipItem(src=os.path.abspath(args.source)))

    # Sizing: --instance-type (exact) > --cpu/--ram (raw) > --gpu/--size (preset) > config.
    gpu_count, gpu_type = _parse_gpu_arg(args.gpu) if args.gpu else (0, None)
    if args.instance_type:
        unit.hardware = VMSpec(type=args.instance_type)
    elif args.cpu or args.ram:
        unit.hardware = ExpectedSpecs(cpu=args.cpu or 2, ram_gb=args.ram or 8)
    elif args.size:
        unit.hardware = provider.preset_specs(args.size)
    elif (gpu_count or gpu_type) and not had_config:
        unit.hardware = ExpectedSpecs()       # GPU box; the SKU bundles cpu/ram
    # GPU layers onto whatever ExpectedSpecs we ended up with (incl. from config).
    if (gpu_count or gpu_type) and isinstance(unit.hardware, ExpectedSpecs):
        unit.hardware.gpu = gpu_count or 1
        unit.hardware.gpu_type = gpu_type

    if args.image:
        unit.image = args.image
    if args.build:
        unit.build = os.path.abspath(args.build)
    if args.registry:
        unit.registry = args.registry

    _apply_overlays(args, infra)
    deploy(infra, ssh_key_path=args.ssh_key)


def cmd_sizes(args):
    try:
        gpu_count, gpu_type = _parse_gpu_arg(args.gpu) if args.gpu else (0, None)
        sizes = get_provider(args.provider).list_sizes(
            args.location, min_cpu=args.cpu, min_ram_gb=args.ram,
            gpu=gpu_count, gpu_type=gpu_type,
        )
        print(f"{'NAME':<30} {'CPU':>4} {'RAM GB':>8} {'GPU':>4} {'$/HR':>8}")
        print("-" * 60)
        for s in sizes[:20]:
            print(f"{s['name']:<30} {s['cpu']:>4} {s['ram_gb']:>8.1f} "
                  f"{s.get('gpus', 0):>4} {s['price']:>8.4f}")
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


def cmd_auth(args):
    provider = get_provider(args.provider)

    # RunPod: a single API key. Azure: service-principal flags (non-interactive,
    # secret also accepted via ARM_CLIENT_SECRET), else the device-code flow.
    secret = args.client_secret or os.environ.get("ARM_CLIENT_SECRET")
    api_key = args.api_key or os.environ.get("RUNPOD_API_KEY")
    sp_provided = any([args.client_id, secret, args.tenant_id, args.subscription_id])
    try:
        if api_key:
            path = provider.save_credentials(api_key=api_key)
            print(f"Credentials saved to {path}")
        elif sp_provided:
            path = provider.save_credentials(
                client_id=args.client_id,
                client_secret=secret,
                tenant_id=args.tenant_id,
                subscription_id=args.subscription_id,
            )
            print(f"Credentials saved to {path}")
        else:
            provider.authenticate()
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


def cmd_down(args):
    for name in args.names:
        try:
            destroy(name, purge=not args.keep_history)
            print(f"Deployment '{name}' destroyed.")
        except Exception as e:
            print(f"error: {e}")
            sys.exit(1)


def cmd_pause(args):
    from ..pipeline import pause
    try:
        pause(args.name)
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


def cmd_resume(args):
    from ..pipeline import resume
    try:
        resume(args.name)
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


def cmd_connect(args):
    from ..pipeline import get
    d = get(args.name)
    if not d or not d.ip:
        print(f"error: deployment '{args.name}' not found")
        sys.exit(1)
    ssh_args = ["ssh", "-i", d.ssh_key, "-o", "StrictHostKeyChecking=no", f"{d.user}@{d.ip}"]
    if args.exec:
        ssh_args.append(args.exec)
    os.execvp("ssh", ssh_args)


def cmd_logs(args):
    from ..pipeline import get, logs
    d = get(args.name)
    if not d or not d.ip:
        print(f"error: deployment '{args.name}' not found")
        sys.exit(1)
    if args.follow:
        from ..core.transfer import open_ssh
        client = open_ssh(d.ip, d.ssh_key)
        cmd = f"sudo journalctl -u {args.name} -n {args.lines} -f --no-pager"
        _, stdout, _ = client.exec_command(cmd)
        try:
            for line in iter(stdout.readline, ""):
                print(line.rstrip("\n"))
        except KeyboardInterrupt:
            pass
        finally:
            client.close()
    else:
        print(logs(args.name, lines=args.lines))


def cmd_list(args):
    deployments = list_deployments()
    if not deployments:
        if not args.names:
            print("No deployments found.")
        return
    if args.names:
        for d in deployments:
            print(d.name)
        return
    fmt = "{:<20} {:<16} {:<40} {}"
    print(fmt.format("NAME", "IP", "URL", "SSH KEY"))
    print("-" * 100)
    for d in deployments:
        print(fmt.format(d.name, d.ip or "-", d.url or "-", d.ssh_key or "-"))


def _configure_logging(verbosity: int):
    """Route library diagnostics to stderr when -v is given (never stdout)."""
    if not verbosity:
        return
    level = logging.DEBUG if verbosity >= 2 else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("infra_lib")
    root.setLevel(level)
    root.addHandler(handler)


def main():
    progress.set_reporter(ConsoleReporter())

    parser = argparse.ArgumentParser(prog="infra-lib", description="Deploy a directory to the cloud.")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="Diagnostics to stderr (-v info, -vv debug)")
    subparsers = parser.add_subparsers(dest="command")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy a directory")
    p_deploy.add_argument("source", nargs="?", default=None, help="Path to the directory to deploy (optional)")
    p_deploy.add_argument("--name", default="default", help="Deployment name (default: default)")
    p_deploy.add_argument("--type", default=None, choices=["vm", "pod"],
                          help="Unit type: vm (a box to fill) or pod (a container host). "
                               "Defaults from config, or 'pod' when --image/--build is given.")
    p_deploy.add_argument("--provider", default=None, choices=provider_names(),
                          help="Cloud provider (default: derived from --type — vm->azure, pod->runpod)")
    p_deploy.add_argument("--location", default="CentralUS")
    p_deploy.add_argument("--ssh-key", default=None)
    p_deploy.add_argument("--domain", default=None)
    p_deploy.add_argument("--domain-strategy", default=None, choices=["own", "cloudflare", "http"])
    p_deploy.add_argument("--proxied", action="store_true")
    p_deploy.add_argument("--cloudflare-token", default=None)
    p_deploy.add_argument("--install", default=None, help="Shell command to run on the VM after deploy")
    p_deploy.add_argument("--start", default=None, help="Command to run as a supervised systemd service")
    p_deploy.add_argument("--port", type=int, default=None, help="App port to expose via reverse proxy")
    p_deploy.add_argument("--size", default=None, metavar="SIZE",
                          help=f"Size preset (azure: {', '.join(_DEFAULT_PRESETS)}; "
                               f"validated against the chosen provider)")
    p_deploy.add_argument("--image", default=None, metavar="REF",
                          help="Container image to boot, e.g. ghcr.io/me/app:latest (implies a pod)")
    p_deploy.add_argument("--build", default=None, metavar="DIR",
                          help="Build an image from DIR (must contain a Dockerfile) and push it")
    p_deploy.add_argument("--registry", default=None, metavar="REG",
                          help="Push target for --build, e.g. ghcr.io/me")
    p_deploy.add_argument("--instance-type", default=None, metavar="SKU",
                          help="Exact instance type, e.g. Standard_D2s_v3 (skips size resolution)")
    p_deploy.add_argument("--cpu", type=int, default=None, help="Minimum vCPUs (resolved to a size)")
    p_deploy.add_argument("--ram", type=float, default=None, help="Minimum RAM in GB (resolved to a size)")
    p_deploy.add_argument("--gpu", default=None, metavar="N|TYPE",
                          help="Request a GPU box: a count (2) or a type (t4, a10, a100). "
                               "Installs the NVIDIA driver automatically.")
    p_deploy.add_argument("--storage", type=int, default=None, metavar="GB",
                          help="Disk size in GB (default 30)")
    p_deploy.add_argument("--config", nargs="?", const="", default=None, metavar="FILE",
                          help="Config file to use. Omit path to open an editor.")
    p_deploy.add_argument("--no-config", action="store_true",
                          help="Ignore any infra.yml in the current directory")

    # sizes
    p_sizes = subparsers.add_parser("sizes", help="List available VM sizes for given specs")
    p_sizes.add_argument("--provider", default="azure", choices=provider_names())
    p_sizes.add_argument("--location", default="CentralUS")
    p_sizes.add_argument("--cpu", type=int, default=1)
    p_sizes.add_argument("--ram", type=float, default=1)
    p_sizes.add_argument("--gpu", default=None, metavar="N|TYPE",
                         help="Only GPU sizes: a count (2) or a type (t4, a10, a100)")

    # auth
    p_auth = subparsers.add_parser(
        "auth",
        help="Authenticate with a cloud provider",
        description="Interactive device-code flow by default; pass service-principal "
                    "flags for non-interactive auth with an existing SP.",
    )
    p_auth.add_argument("provider", choices=provider_names(), help="Cloud provider to authenticate with")
    p_auth.add_argument("--api-key", default=None, help="API key (RunPod; or set RUNPOD_API_KEY)")
    p_auth.add_argument("--client-id", default=None, help="Existing service principal app/client ID")
    p_auth.add_argument("--client-secret", default=None,
                        help="SP client secret (or set ARM_CLIENT_SECRET)")
    p_auth.add_argument("--tenant-id", default=None, help="Azure tenant ID")
    p_auth.add_argument("--subscription-id", default=None, help="Azure subscription ID")

    # down
    p_down = subparsers.add_parser("down", help="Destroy a deployment")
    p_down.add_argument("names", nargs="+", metavar="NAME", help="Deployment name(s) to destroy")
    p_down.add_argument("--keep-history", action="store_true", help="Keep Pulumi stack history and config")

    # pause / resume
    p_pause = subparsers.add_parser(
        "pause", help="Stop a deployment's VM (deallocate) — keeps the disk, halts compute billing")
    p_pause.add_argument("name", help="Deployment name")
    p_resume = subparsers.add_parser("resume", help="Start a paused deployment back up")
    p_resume.add_argument("name", help="Deployment name")

    # connect
    p_connect = subparsers.add_parser("connect", help="SSH into a deployment")
    p_connect.add_argument("name", help="Deployment name")
    p_connect.add_argument("-e", "--exec", default=None, metavar="CMD",
                           help="Run a command instead of opening an interactive shell")

    # logs
    p_logs = subparsers.add_parser("logs", help="Show service logs for a deployment")
    p_logs.add_argument("name", help="Deployment name")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines to show (default 50)")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Stream new log lines as they arrive")

    # list
    p_list = subparsers.add_parser("list", help="List all deployments")
    p_list.add_argument("-n", "--names", action="store_true", help="Print names only, one per line")

    args = parser.parse_args()
    _configure_logging(args.verbose)

    if args.command == "sizes":
        cmd_sizes(args)
    elif args.command == "auth":
        cmd_auth(args)
    elif args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "down":
        cmd_down(args)
    elif args.command == "pause":
        cmd_pause(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "connect":
        cmd_connect(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "list":
        cmd_list(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
