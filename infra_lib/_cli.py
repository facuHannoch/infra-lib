import argparse
import os
import sys
from . import deploy
from ._domain import BYODomain, CloudflareDomain
from ._provision import list_deployments


def cmd_deploy(args):
    strategy = args.domain_strategy
    if args.domain and strategy is None:
        strategy = "own"
    if not args.domain and strategy not in (None, "http"):
        print("error: --domain is required when using --domain-strategy own or cloudflare")
        sys.exit(1)

    domain = None
    try:
        if strategy == "own":
            domain = BYODomain(name=args.domain, proxied=args.proxied)
        elif strategy == "cloudflare":
            if not args.cloudflare_token:
                print("error: --cloudflare-token is required when using --domain-strategy cloudflare")
                sys.exit(1)
            domain = CloudflareDomain(name=args.domain, api_token=args.cloudflare_token, proxied=args.proxied)
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)

    result = deploy(
        source=args.source,
        name=args.name,
        domain=domain,
        location=args.location,
        ssh_key_path=args.ssh_key,
    )
    print(f"Deployed successfully.")
    print(f"  IP:  {result.ip}")
    print(f"  URL: {result.url}")


def cmd_list(args):
    deployments = list_deployments()
    if not deployments:
        print("No deployments found.")
        return
    fmt = "{:<20} {:<16} {}"
    print(fmt.format("NAME", "IP", "URL"))
    print("-" * 60)
    for d in deployments:
        print(fmt.format(d["name"], d.get("ip", "-"), d.get("url", "-")))


def main():
    parser = argparse.ArgumentParser(prog="infra-lib", description="Deploy a directory to the cloud.")
    subparsers = parser.add_subparsers(dest="command")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy a directory")
    p_deploy.add_argument("source", help="Path to the directory to deploy")
    p_deploy.add_argument("--name", default="default", help="Deployment name (default: default)")
    p_deploy.add_argument("--provider", default="azure", choices=["azure"])
    p_deploy.add_argument("--location", default="CentralUS")
    p_deploy.add_argument("--ssh-key", default=None)
    p_deploy.add_argument("--domain", default=None)
    p_deploy.add_argument("--domain-strategy", default=None, choices=["own", "cloudflare", "http"])
    p_deploy.add_argument("--proxied", action="store_true")
    p_deploy.add_argument("--cloudflare-token", default=None)

    # list
    p_list = subparsers.add_parser("list", help="List all deployments")

    args = parser.parse_args()

    if args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "list":
        cmd_list(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
