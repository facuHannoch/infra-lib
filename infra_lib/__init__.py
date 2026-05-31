"""infra-lib: point at a directory (or an install command) and get back a URL.

Public API (suitable for programmatic / MCP use; silent by default):

    import infra_lib
    d = infra_lib.deploy(name="myapp", vm="small", port=3000,
                         ship=["."], setup=["..."])
    print(d.url, d.ip, d.ssh_key)

    infra_lib.list_deployments()
    infra_lib.get("myapp")
    infra_lib.run("myapp", "tail -n 50 /srv/logs/app.log")
    infra_lib.down("myapp")
"""
from .models import Deployment, Service, VMSpec, ResolvedSize
from .core.domain import Domain, BYODomain, CloudflareDomain
from .pipeline import deploy, get, list_deployments, run, down
from . import progress

__all__ = [
    "deploy",
    "get",
    "list_deployments",
    "run",
    "down",
    "Deployment",
    "Service",
    "VMSpec",
    "ResolvedSize",
    "Domain",
    "BYODomain",
    "CloudflareDomain",
    "progress",
]
