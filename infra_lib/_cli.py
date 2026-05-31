import argparse
import os
import sys
from . import deploy
from ._domain import BYODomain, CloudflareDomain
from ._provision import list_deployments, destroy
from ._auth import auth_azure, load_azure_credentials
from ._resolve import resolve_azure_size, AZURE_PRESETS
from ._spec import VMSpec
from ._tui import prompt_vm_spec
from ._config import load_config


def cmd_deploy(args):
    # Load infra.yml if present; CLI flags override config values
    cfg = load_config()

    source = args.source
    name = args.name if args.name != "default" else (cfg.name if cfg else "default")
    location = args.location if args.location != "CentralUS" else (cfg.location if cfg else "CentralUS")
    ship = cfg.ship if cfg else []

    if source and not os.path.isdir(source):
        print(f"error: source must be a directory: {source}")
        sys.exit(1)

    # Domain: CLI flags win, fall back to config
    raw_domain = args.domain or (cfg.domain if cfg else None)
    strategy = args.domain_strategy or (cfg.domain_strategy if cfg else None)
    proxied = args.proxied or (cfg.proxied if cfg else False)
    if raw_domain and strategy is None:
        strategy = "own"
    if not raw_domain and strategy not in (None, "http"):
        print("error: --domain is required when using --domain-strategy own or cloudflare")
        sys.exit(1)

    domain = None
    try:
        if strategy == "own":
            domain = BYODomain(name=raw_domain, proxied=proxied)
        elif strategy == "cloudflare":
            if not args.cloudflare_token:
                print("error: --cloudflare-token is required when using --domain-strategy cloudflare")
                sys.exit(1)
            domain = CloudflareDomain(name=raw_domain, api_token=args.cloudflare_token, proxied=proxied)
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)

    # VM spec: --vm flag > config > TUI prompt
    vm_label = args.vm or (cfg.vm if cfg else None)
    if vm_label:
        preset = AZURE_PRESETS.get(vm_label)
        if not preset:
            print(f"error: unknown VM size '{vm_label}'. Choose from: {', '.join(AZURE_PRESETS)}")
            sys.exit(1)
        vm_spec = VMSpec(cpu=preset["cpu"], ram_gb=preset["ram_gb"])
    else:
        vm_spec = prompt_vm_spec()

    setup = cfg.setup if cfg else []
    if args.install:
        setup = setup + [args.install]

    port = args.port or (cfg.port if cfg else None)

    result = deploy(
        source=source,
        name=name,
        domain=domain,
        location=location,
        ssh_key_path=args.ssh_key,
        ship=ship,
        setup=setup,
        vm=vm_spec,
        port=port,
    )


def cmd_sizes(args):
    try:
        load_azure_credentials()
        from ._resolve import _azure_size_specs, _azure_list_sizes
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


def _make_credential():
    from azure.identity import ClientSecretCredential
    import os
    return ClientSecretCredential(
        tenant_id=os.environ["ARM_TENANT_ID"],
        client_id=os.environ["ARM_CLIENT_ID"],
        client_secret=os.environ["ARM_CLIENT_SECRET"],
    )


def cmd_auth(args):
    if args.provider == "azure":
        try:
            auth_azure()
        except Exception as e:
            print(f"error: {e}")
            sys.exit(1)
    else:
        print(f"error: unsupported provider '{args.provider}'")
        sys.exit(1)


def cmd_down(args):
    try:
        destroy(args.name)
        print(f"Deployment '{args.name}' destroyed.")
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


def cmd_list(args):
    deployments = list_deployments()
    if not deployments:
        print("No deployments found.")
        return
    fmt = "{:<20} {:<16} {:<40} {}"
    print(fmt.format("NAME", "IP", "URL", "SSH KEY"))
    print("-" * 100)
    for d in deployments:
        print(fmt.format(d["name"], d.get("ip", "-"), d.get("url", "-"), d.get("ssh_key", "-")))


def main():
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
    p_deploy.add_argument("--port", type=int, default=None, help="App port to expose via reverse proxy")
    p_deploy.add_argument("--vm", default=None, choices=list(AZURE_PRESETS), metavar="SIZE",
                          help=f"VM size preset: {', '.join(AZURE_PRESETS)} (skips interactive prompt)")

    # sizes
    p_sizes = subparsers.add_parser("sizes", help="List available VM sizes for given specs")
    p_sizes.add_argument("--provider", default="azure", choices=["azure"])
    p_sizes.add_argument("--location", default="CentralUS")
    p_sizes.add_argument("--cpu", type=int, default=1)
    p_sizes.add_argument("--ram", type=float, default=1)

    # auth
    p_auth = subparsers.add_parser("auth", help="Authenticate with a cloud provider")
    p_auth.add_argument("provider", choices=["azure"], help="Cloud provider to authenticate with")

    # down
    p_down = subparsers.add_parser("down", help="Destroy a deployment")
    p_down.add_argument("--name", default="default", help="Deployment name to destroy (default: default)")

    # list
    p_list = subparsers.add_parser("list", help="List all deployments")

    args = parser.parse_args()

    if args.command == "sizes":
        cmd_sizes(args)
    elif args.command == "auth":
        cmd_auth(args)
    elif args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "down":
        cmd_down(args)
    elif args.command == "list":
        cmd_list(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
