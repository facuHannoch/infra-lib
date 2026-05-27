import argparse
import os
import sys
from . import deploy
from ._domain import BYODomain, CloudflareDomain


def main():
    parser = argparse.ArgumentParser(prog="infra-lib", description="Deploy a directory to the cloud.")
    parser.add_argument("source", help="Path to the directory to deploy")
    parser.add_argument("--provider", default="azure", choices=["azure"], help="Cloud provider (default: azure)")
    parser.add_argument("--location", default="CentralUS", help="Cloud region (default: CentralUS)")
    parser.add_argument("--ssh-key", default=None, help="Path to SSH private key (default: ~/.ssh/id_rsa)")
    parser.add_argument("--domain", default=None, help="Domain name (e.g. mysite.com)")
    parser.add_argument(
        "--domain-strategy",
        default=None,
        choices=["own", "cloudflare", "http"],
        help="Domain strategy: own (BYO), cloudflare (auto DNS), http (no domain). Defaults to 'own' if --domain is set.",
    )
    parser.add_argument("--proxied", action="store_true", help="Domain is proxied through Cloudflare")
    parser.add_argument("--cloudflare-token", default=None, help="Cloudflare API token (required for --domain-strategy cloudflare)")

    args = parser.parse_args()

    # resolve domain strategy
    strategy = args.domain_strategy
    if args.domain and strategy is None:
        strategy = "own"
    if not args.domain and strategy not in (None, "http"):
        parser.error("--domain is required when using --domain-strategy own or cloudflare")

    domain = None
    if strategy == "own":
        domain = BYODomain(name=args.domain, proxied=args.proxied)
    elif strategy == "cloudflare":
        if not args.cloudflare_token:
            parser.error("--cloudflare-token is required when using --domain-strategy cloudflare")
        domain = CloudflareDomain(name=args.domain, api_token=args.cloudflare_token, proxied=args.proxied)

    result = deploy(
        source=args.source,
        domain=domain,
        location=args.location,
        ssh_key_path=args.ssh_key,
    )

    print(f"Deployed successfully.")
    print(f"  IP:  {result.ip}")
    print(f"  URL: {result.url}")


if __name__ == "__main__":
    main()
