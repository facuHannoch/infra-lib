"""
infra-lib guide — printed by `infra-lib guide`.

Written for LLM agents: concise, example-heavy, no fluff.
"""

GUIDE = """\
# infra-lib — LLM reference

Deploy VMs and GPU pods to Azure and RunPod from the CLI or Python API.
Two unit types: `vm` (a Linux box you fill) and `pod` (a container host).

---

## LLM agent: start here

```bash
infra-lib auth status          # 1. check what providers are authenticated
infra-lib guide                # 2. (this output) — read once, then act
infra-lib deploy ...           # 3. deploy
infra-lib list                 # 4. inspect
infra-lib connect NAME -e CMD  # 5. run remote commands (non-interactive)
infra-lib down NAME            # 6. destroy when done
```

Do **not** run `infra-lib sizes --help` or poke individual subcommands to learn the
API — this guide has everything. Check auth status before deploying to avoid a
deploy that fails halfway through with a credentials error.

---

## Auth

```
infra-lib auth status                         # check what is configured (start here)

infra-lib auth azure                          # interactive device-code flow
infra-lib auth azure --client-id X \\
  --client-secret S --tenant-id T \\
  --subscription-id U                         # non-interactive / CI
infra-lib auth runpod --api-key <KEY>         # or set RUNPOD_API_KEY
```

`auth status` prints one line per provider:

```
azure      ✓ configured         subscription=xxx  tenant=yyy
runpod     ✗ not configured     run: infra-lib auth runpod --api-key KEY
```

**For LLM agents: always run `auth status` before attempting a deploy** to confirm
which providers are available — avoids starting a deploy that will fail on missing creds.

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
| `--build DIR` | — | Build+push image from DIR (must contain a Dockerfile) |
| `--dockerfile FILE\|-` | — | Dockerfile path; `-` reads stdin; omit path opens editor. Implies pod. |
| `--registry REG` | — | Push target for `--build`/`--dockerfile`, e.g. `ghcr.io/me` |
| `--port N` | — | Your **app's internal port** (what your process binds to). NOT 80/443 — infra-lib handles those. |
| `--domain NAME` | — | Domain to serve on, e.g. `ffacu.dev`. Caddy handles TLS automatically — do NOT install nginx/certbot. |
| `--domain-strategy S` | — | `own` (you manage DNS), `cloudflare` (auto DNS update), `http` (no domain) |
| `--start CMD` | — | Long-running command (systemd service on vm; container CMD on pod) |
| `--setup CMD` | — | One-off command that runs once after deploy, before start. Must exit. |
| `--storage GB` | 30 | Disk / volume size |
| `--location REGION` | CentralUS | Azure region or RunPod datacenter hint |
| `--ssh-key PATH` | auto-generated | Path to private key |
| `--instance-type SKU` | — | Exact SKU, e.g. `Standard_D2s_v3` (skips size resolution) |
| `[source]` | — | Local directory to rsync onto the machine |

### --dockerfile: write or pipe a Dockerfile inline (agent-friendly)

Three modes — all create a temp build context and imply a pod:

```
# 1. Explicit path
infra-lib deploy --dockerfile ./Dockerfile --registry ghcr.io/me --name mypod

# 2. Read from stdin (best for agents / scripts — no temp file needed)
echo "FROM python:3.11-slim
CMD python3 -m http.server 8000" | infra-lib deploy --dockerfile - \\
  --registry ghcr.io/me --name mypod --port 8000

# 3. Open $EDITOR (human workflow, same pattern as --config)
infra-lib deploy --dockerfile --registry ghcr.io/me --name mypod
```

**For LLM agents:** use `--dockerfile -` and pipe the content via stdin.
You can also write to a temp file and pass its path — both work.

### Ship files (rsync)

Pass a directory as the first positional arg, or use `ship:` in config.
Files land at `/srv/files/<name>` by default; override with `src:dest` syntax
in the config file.

**Always excluded (never transferred, regardless of .gitignore):**
`.git`, `__pycache__`, `.venv`, `venv`, `*.pyc`,
`node_modules`, `.next`, `.nuxt`, `.svelte-kit`,
`dist`, `out`, `build`, `target`,
`.env`, `.env.*`

`.gitignore` files in the source tree are also respected.
Safe to ship a Next.js or Python project directly — no 800 MB `node_modules` surprise.

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
- **Starts as a bare Ubuntu 22.04 VM.** Nothing is pre-installed except Caddy (for
  `--domain`) and the SSH server. Node, Python runtimes, compilers, etc. must be
  installed in `--setup`. Always begin with `apt-get update && apt-get install -y ...`.

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

Deploy a Next.js blog to a domain (bare VM — must install Node first):

```
infra-lib deploy ./blog --name my-blog \\
  --size small --port 3000 \\
  --domain example.com --domain-strategy own \\
  --setup "apt-get update -qq && apt-get install -y nodejs npm && cd /srv/files/blog && npm install && npm run build" \\
  --start "cd /srv/files/blog && npm run start"
```

Point your DNS A record at the printed IP. Caddy obtains TLS automatically.
`node_modules` and `.next` are never transferred — always build on the VM.

Deploy a VM with a local Python project:

```
infra-lib deploy ./myproject --name webapp \\
  --size small --port 8000 \\
  --setup "apt-get update -qq && apt-get install -y python3 python3-pip && pip install -r /srv/files/myproject/requirements.txt" \\
  --start "python3 /srv/files/myproject/app.py"
```

---

## Common mistakes

**Wrong: forgetting apt-get update / runtime install**
```bash
--setup "npm install"   ✗  (Node isn't on the VM — apt-get install nodejs first)
--setup "apt-get update -qq && apt-get install -y nodejs npm && npm install"   ✓
```
The VM is bare Ubuntu 22.04. Install every runtime your app needs in `--setup`.

**Wrong: passing 80/443 as the port**
```
--port 80 --port 443   ✗  (--port only takes one value, and it's your app port)
--port 3000            ✓  (Caddy listens on 80/443 and proxies to 3000)
```

**Wrong: installing nginx or certbot in setup**
```bash
apt-get install -y nginx certbot   ✗  (conflicts with Caddy, which infra-lib already runs)
```
If you pass `--domain`, TLS and the reverse proxy are handled automatically.

**Wrong: shipping build output**
```
infra-lib deploy ./blog   # .next/, node_modules/ are auto-excluded — build on the VM instead
```
Ship the source, then build in `--setup`:
```
--setup "... && npm install && npm run build"
```

**Wrong: using --setup for the long-running process**
```
--setup "node server.js"   ✗  (blocks; setup commands must exit)
--start "node server.js"   ✓  (runs as a supervised systemd service)
```
"""
