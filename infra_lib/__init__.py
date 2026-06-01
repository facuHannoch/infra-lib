"""infra-lib: describe your infrastructure and get back a running deployment.

Public API (suitable for programmatic / MCP use; silent by default):

    import infra_lib
    from infra_lib import Infrastructure, Machine, ExpectedSpecs, VMSpec, Disk

    infra = Infrastructure(
        name="myapp",
        machines=[Machine(hardware=ExpectedSpecs(cpu=2, ram_gb=8),   # or VMSpec(type="Standard_D2s_v3")
                          disk=Disk(size_gb=30),
                          ship=["."], start="...", ports=[3000])],
    )
    d = infra_lib.deploy(infra)   # resolves hardware to a concrete VMSpec, then provisions
    print(d.url, d.ip, d.ssh_command)

    infra_lib.list_deployments()
    infra_lib.get("myapp")
    infra_lib.logs("myapp")
    infra_lib.run("myapp", "tail -n 50 /srv/logs/app.log")
    infra_lib.connect("myapp")
    infra_lib.down("myapp")
"""
from .models import Infrastructure, Machine, ExpectedSpecs, VMSpec, Disk, Deployment, Service
from .core.domain import Domain, BYODomain, CloudflareDomain, build_domain
from .pipeline import deploy, get, list_deployments, run, logs, connect, down
from . import progress

__all__ = [
    "deploy",
    "get",
    "list_deployments",
    "run",
    "logs",
    "connect",
    "down",
    "Infrastructure",
    "Machine",
    "ExpectedSpecs",
    "VMSpec",
    "Disk",
    "Deployment",
    "Service",
    "Domain",
    "BYODomain",
    "CloudflareDomain",
    "build_domain",
    "progress",
]
