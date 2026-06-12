"""
infra-lib guide — printed by `infra-lib guide`.

Written for LLM agents: concise, example-heavy, no fluff.
"""

GUIDE = """\
# infra-lib — LLM reference

Deploy VMs and GPU pods to Azure and RunPod from the CLI or Python API.
Two unit types: `vm` (a Linux box you fill) and `pod` (a container host).

---

## Auth

```
infra-lib auth azure                          # interactive device-code flow
infra-lib auth azure --client-id X \\
  --client-secret S --tenant-id T \\
  --subscription-id U                         # non-interactive / CI
infra-lib auth runpod --api-key <KEY>         # or set RUNPOD_API_KEY
```

---

## Deploy

### Minimal

```
infra-lib deploy --name myapp --type vm --size small --start "python3 -m http.server 8000" --port 8000
infra-lib deploy --name mypod --type pod --gpu a40 --image runpod/base:0.6.0 --port 8000
```

### Common flags

| Flag | Default | Notes |
|---|---|---|
| `--name NAME` | `default` | Deployment name |
| `--type vm\|pod` | derived | `vm`→azure, `pod`→runpod |
| `--provider azure\|runpod` | derived from type | Override provider |
| `--size small\|medium\|large` | — | Named preset |
| `--cpu N` | — | Minimum vCPUs |
| `--ram N` | — | Minimum RAM (GB) |
| `--gpu N\|TYPE` | — | GPU count or name: `a40`, `a100`, `h100`, `l40s`, `rtxa6000` |
| `--image REF` | — | Container image (implies `--type pod`) |
| `--build DIR` | — | Build+push image from DIR (needs Dockerfile) |
| `--registry REG` | — | Push target for `--build`, e.g. `ghcr.io/me` |
| `--port N` | — | Expose port via reverse proxy |
| `--start CMD` | — | Long-running command (systemd service on vm; container CMD on pod) |
| `--install CMD` | — | One-off setup command (runs after deploy, before start) |
| `--storage GB` | 30 | Disk / volume size |
| `--location REGION` | CentralUS | Azure region or RunPod datacenter hint |
| `--ssh-key PATH` | auto-generated | Path to private key |
| `--instance-type SKU` | — | Exact SKU, e.g. `Standard_D2s_v3` (skips size resolution) |
| `[source]` | — | Local directory to rsync onto the machine |

### Ship files (rsync)

Pass a directory as the first positional arg, or use `ship:` in config.
Files land at `/srv/files/<name>` by default; override with `src:dest` syntax
in the config file.

### infra.yml config file (auto-loaded from cwd)

```yaml
name: myapp
location: CentralUS

units:
  - type: vm
    size: small
    ports: [8000]
    start: "python3 -m http.server 8000"
    ship:
      - ./src:/srv/files/src
    setup:
      - "pip install -r /srv/files/src/requirements.txt"

# or a GPU pod:
  - type: pod
    hardware:
      gpu: 1
      gpu_type: a40
    image: runpod/base:0.6.0
    ports: [8000]
    start: "python3 server.py"
```

---

## Lifecycle

```
infra-lib list                                # all deployments (name / ip / url / key)
infra-lib list -n                             # names only
infra-lib connect myapp                       # interactive SSH
infra-lib connect myapp -e "ls /srv/files"    # run a command, close connection
infra-lib logs myapp                          # last 50 log lines
infra-lib logs myapp -f                       # follow
infra-lib pause myapp                         # stop compute, keep disk (no billing)
infra-lib resume myapp                        # restart from paused state
infra-lib down myapp                          # destroy everything
```

---

## Python API

```python
import infra_lib

# Deploy
infra_lib.deploy(infra_lib.Infrastructure(
    name="myapp",
    units=[infra_lib.Unit(
        type="vm",
        hardware=infra_lib.ExpectedSpecs(cpu=2, ram_gb=8),
        ports=[8000],
        start="python3 -m http.server 8000",
    )],
))

# Inspect
d = infra_lib.get("myapp")    # Deployment | None
# d.name, d.ip, d.url, d.ssh_key, d.user

deployments = infra_lib.list_deployments()   # list[Deployment]

# Run a remote command (agent-friendly, non-interactive)
output = infra_lib.run("myapp", "cat /srv/files/marker.txt")

# Lifecycle
infra_lib.pause("myapp")
infra_lib.resume("myapp")
infra_lib.destroy("myapp")
```

---

## Provider notes

### Azure (vm)
- Sizes: `small` (2cpu/4GB), `medium` (4cpu/8GB), `large` (8cpu/16GB), `xlarge` (16cpu/32GB)
- GPU: supply `--gpu a100` etc. — requires an approved quota on your subscription
- Disk survives pause/resume; public IP may change on resume

### RunPod (pod)
- GPU catalog: `a40`, `rtxa6000`, `rtx6000ada`, `l40`, `l40s`, `a100`, `h100`
- Volume mounted at `/workspace` — **only `/workspace` survives pause/resume**
- Public URL: `https://<pod-id>-<port>.proxy.runpod.net`
- SSH available when `ship` or `setup` is configured (PUBLIC_KEY injected at create)

---

## Auth locations

Credentials are stored in `~/.infra-lib/credentials` (ini format, chmod 600).
Each provider has its own section: `[azure]`, `[runpod]`.
Env overrides: `RUNPOD_API_KEY`, `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`,
`ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`.

---

## Sizes reference

```
infra-lib sizes --provider azure --location CentralUS
infra-lib sizes --provider azure --gpu a100
infra-lib sizes --provider runpod --gpu 1
```

---

## Quick examples

Deploy a GPU pod, run a command, destroy:

```
infra-lib auth runpod --api-key $KEY
infra-lib deploy --name test --type pod --gpu a40 \\
  --image runpod/base:0.6.0 --port 8000 \\
  --start "python3 -m http.server 8000"
infra-lib connect test -e "echo hello from pod"
infra-lib down test
```

Deploy a VM with a local project:

```
infra-lib deploy ./myproject --name webapp \\
  --size small --port 8000 \\
  --install "pip install -r /srv/files/myproject/requirements.txt" \\
  --start "python3 /srv/files/myproject/app.py"
```
"""
