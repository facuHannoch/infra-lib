import argparse
import os
import sys

from .. import progress
from ..pipeline import deploy, list_deployments, destroy
from ..models import Infrastructure, ExpectedSpecs, VMSpec
from ..core.domain import build_domain
from ..providers.azure.auth import auth_azure, load_azure_credentials
from ..providers.azure.sizes import AZURE_PRESETS, expectedspecs_from_preset
from ..config import load_config
from .tui import prompt_vm_spec
from .reporter import ConsoleReporter


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


def cmd_deploy(args):
    infra = _resolve_config(args)
    had_config = infra is not None
    infra = infra or Infrastructure()
    machine = infra.machines[0]

    if args.name != "default":
        infra.name = args.name
    if args.location != "CentralUS":
        infra.location = args.location

    if args.source:
        if not os.path.isdir(args.source):
            print(f"error: source must be a directory: {args.source}")
            sys.exit(1)
        machine.ship.append(os.path.abspath(args.source))

    # Sizing: --instance-type (exact) > --cpu/--ram (raw) > --vm (preset) > config > prompt.
    if args.instance_type:
        machine.hardware = VMSpec(type=args.instance_type)
    elif args.cpu or args.ram:
        machine.hardware = ExpectedSpecs(cpu=args.cpu or 2, ram_gb=args.ram or 8)
    elif args.vm:
        machine.hardware = expectedspecs_from_preset(args.vm)
    elif not had_config:
        hardware, prompted_storage = prompt_vm_spec(infra.location)
        machine.hardware = hardware
        machine.disk.size_gb = prompted_storage
    if args.storage:
        machine.disk.size_gb = args.storage

    if args.install:
        machine.setup.append(args.install)
    if args.start:
        machine.start = args.start
    if args.port:
        machine.ports = [args.port]

    # Domain: CLI flags rebuild it; otherwise keep whatever config produced.
    if args.domain or args.domain_strategy:
        try:
            machine.domain = build_domain(
                name=args.domain,
                strategy=args.domain_strategy,
                proxied=args.proxied,
                cloudflare_token=args.cloudflare_token,
            )
        except ValueError as e:
            print(f"error: {e}")
            sys.exit(1)

    deploy(infra, ssh_key_path=args.ssh_key)


def _make_credential():
    from azure.identity import ClientSecretCredential
    return ClientSecretCredential(
        tenant_id=os.environ["ARM_TENANT_ID"],
        client_id=os.environ["ARM_CLIENT_ID"],
        client_secret=os.environ["ARM_CLIENT_SECRET"],
    )


def cmd_sizes(args):
    try:
        load_azure_credentials()
        from ..providers.azure.sizes import _azure_size_specs, _azure_list_sizes
        specs = _azure_size_specs(args.location, _make_credential())
        prices = _azure_list_sizes(args.location)
        candidates = [
            s for s in specs
            if s["cpu"] >= args.cpu and s["ram_gb"] >= args.ram and s["name"] in prices
        ]
        candidates.sort(key=lambda s: prices.get(s["name"], 9999))
        print(f"{'NAME':<30} {'CPU':>4} {'RAM GB':>8} {'$/HR':>8}")
        print("-" * 55)
        for s in candidates[:20]:
            print(f"{s['name']:<30} {s['cpu']:>4} {s['ram_gb']:>8.1f} {prices.get(s['name'], 0):>8.4f}")
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


def cmd_auth(args):
    if args.provider != "azure":
        print(f"error: unsupported provider '{args.provider}'")
        sys.exit(1)

    # If any service-principal flag is given, save those creds (non-interactive).
    # The secret may also come from the ARM_CLIENT_SECRET env var to keep it out
    # of shell history. Otherwise fall back to the interactive device-code flow.
    secret = args.client_secret or os.environ.get("ARM_CLIENT_SECRET")
    sp_provided = any([args.client_id, secret, args.tenant_id, args.subscription_id])
    try:
        if sp_provided:
            from ..providers.azure.auth import save_azure_credentials
            path = save_azure_credentials(
                client_id=args.client_id,
                client_secret=secret,
                tenant_id=args.tenant_id,
                subscription_id=args.subscription_id,
            )
            print(f"Credentials saved to {path}")
        else:
            auth_azure()
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


def main():
    progress.set_reporter(ConsoleReporter())

    parser = argparse.ArgumentParser(prog="infra-lib", description="Deploy a directory to the cloud.")
    subparsers = parser.add_subparsers(dest="command")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy a directory")
    p_deploy.add_argument("source", nargs="?", default=None, help="Path to the directory to deploy (optional)")
    p_deploy.add_argument("--name", default="default", help="Deployment name (default: default)")
    p_deploy.add_argument("--provider", default="azure", choices=["azure"])
    p_deploy.add_argument("--location", default="CentralUS")
    p_deploy.add_argument("--ssh-key", default=None)
    p_deploy.add_argument("--domain", default=None)
    p_deploy.add_argument("--domain-strategy", default=None, choices=["own", "cloudflare", "http"])
    p_deploy.add_argument("--proxied", action="store_true")
    p_deploy.add_argument("--cloudflare-token", default=None)
    p_deploy.add_argument("--install", default=None, help="Shell command to run on the VM after deploy")
    p_deploy.add_argument("--start", default=None, help="Command to run as a supervised systemd service")
    p_deploy.add_argument("--port", type=int, default=None, help="App port to expose via reverse proxy")
    p_deploy.add_argument("--vm", default=None, choices=list(AZURE_PRESETS), metavar="SIZE",
                          help=f"VM size preset: {', '.join(AZURE_PRESETS)} (skips interactive prompt)")
    p_deploy.add_argument("--instance-type", default=None, metavar="SKU",
                          help="Exact instance type, e.g. Standard_D2s_v3 (skips size resolution)")
    p_deploy.add_argument("--cpu", type=int, default=None, help="Minimum vCPUs (resolved to a size)")
    p_deploy.add_argument("--ram", type=float, default=None, help="Minimum RAM in GB (resolved to a size)")
    p_deploy.add_argument("--storage", type=int, default=None, metavar="GB",
                          help="Disk size in GB (default 30)")
    p_deploy.add_argument("--config", nargs="?", const="", default=None, metavar="FILE",
                          help="Config file to use. Omit path to open an editor.")
    p_deploy.add_argument("--no-config", action="store_true",
                          help="Ignore any infra.yml in the current directory")

    # sizes
    p_sizes = subparsers.add_parser("sizes", help="List available VM sizes for given specs")
    p_sizes.add_argument("--provider", default="azure", choices=["azure"])
    p_sizes.add_argument("--location", default="CentralUS")
    p_sizes.add_argument("--cpu", type=int, default=1)
    p_sizes.add_argument("--ram", type=float, default=1)

    # auth
    p_auth = subparsers.add_parser(
        "auth",
        help="Authenticate with a cloud provider",
        description="Interactive device-code flow by default; pass service-principal "
                    "flags for non-interactive auth with an existing SP.",
    )
    p_auth.add_argument("provider", choices=["azure"], help="Cloud provider to authenticate with")
    p_auth.add_argument("--client-id", default=None, help="Existing service principal app/client ID")
    p_auth.add_argument("--client-secret", default=None,
                        help="SP client secret (or set ARM_CLIENT_SECRET)")
    p_auth.add_argument("--tenant-id", default=None, help="Azure tenant ID")
    p_auth.add_argument("--subscription-id", default=None, help="Azure subscription ID")

    # down
    p_down = subparsers.add_parser("down", help="Destroy a deployment")
    p_down.add_argument("names", nargs="+", metavar="NAME", help="Deployment name(s) to destroy")
    p_down.add_argument("--keep-history", action="store_true", help="Keep Pulumi stack history and config")

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

    if args.command == "sizes":
        cmd_sizes(args)
    elif args.command == "auth":
        cmd_auth(args)
    elif args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "down":
        cmd_down(args)
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
