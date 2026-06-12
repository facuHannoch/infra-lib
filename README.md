# infra-lib

**Point at a directory (or an image), get back a URL.** `infra-lib` makes deploying
to the cloud dead simple: describe what you want, and it provisions the machine,
puts your code (or container) on it, wires up access, and hands you back a running
deployment.

It is usable three ways from the same core:

- a **CLI** (`infra-lib deploy …`) for humans,
- a **Python API** (`import infra_lib`) for scripts, and
- (planned) an **MCP server** so agents can deploy/manage infrastructure.

Today it ships two providers:

| Provider | Realizes | Good for |
|---|---|---|
| **Azure** | a **`vm`** — a box you fill (ship files, run setup, supervise a process) | web apps, services, static sites, HTTPS domains |
| **RunPod** | a **`pod`** — a container host with GPUs | GPU workloads (LLMs, training), anything packaged as an image |

Clouds sit behind a `Provider` interface, so others slot in without touching the
pipeline.

> **Status note.** The Azure/`vm` path is the mature one. The RunPod/`pod` path is
> implemented (Pulumi + the RunPod SDK) but **not yet exercised live** — it needs an
> API key, GPU availability, and `pip install pulumi-runpod runpod`. See
> [Roadmap & limitations](#roadmap--limitations).

---

## Table of contents

- [The core idea: units & types](#the-core-idea-units--types)
- [How a deploy works](#how-a-deploy-works)
- [Install](#install)
- [Authenticate](#authenticate)
- [Quick start](#quick-start)
- [The `infra.yml` file](#the-infrayml-file)
- [CLI reference](#cli-reference)
- [Choosing a size (vm) or GPU (pod)](#choosing-a-size-vm-or-gpu-pod)
- [Shipping files (`ship`)](#shipping-files-ship)
- [`setup` vs `start`](#setup-vs-start)
- [GPUs](#gpus)
- [Domains & HTTPS](#domains--https)
- [Pause & resume](#pause--resume)
- [Python API](#python-api)
- [What lands on the machine](#what-lands-on-the-machine)
- [Architecture](#architecture)
- [Output, logging & verbosity](#output-logging--verbosity)
- [Files & state on disk](#files--state-on-disk)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Roadmap & limitations](#roadmap--limitations)

---

## The core idea: units & types

Everything you deploy is a **unit**. A unit has a **`type`**:

- **`vm`** — a blank box you *fill*: rsync files, run setup commands, supervise a
  long-running process with systemd, expose it behind Caddy with a domain. (Azure.)
- **`pod`** — a container host that *boots from an image* you give it, exposes a
  port behind a proxy URL, and (for GPU work) hands you a GPU. (RunPod.)

`type` is a **realization detail, not a separate object**. There is one `unit` with
the same fields; which ones apply depends on the type. The deploy pipeline runs the
**same ordered steps for every unit**, and a step simply **no-ops when the substrate
can't do it** — e.g. a pod with no SSH skips the file-shipping step. There are no
separate code paths per type.

The provider is normally **derived from the type** (`vm → azure`, `pod → runpod`),
so you rarely name a cloud directly — you say *what kind of thing* you want.

```yaml
# a vm (Azure): fill a box
type: vm
size: small
ship: [.]
start: bun run server.ts
port: 3000

# a pod (RunPod): boot an image on a GPU
type: pod
gpu: a40
image: ghcr.io/me/llm:latest
port: 11434
```

---

## How a deploy works

A single `deploy` runs this pipeline. Each step is owned either by the **provider**
(the parts that differ by substrate) or is **shared** (run by the pipeline when the
unit's machine exposes SSH):

| # | Step | vm (Azure) | pod (RunPod) |
|---|---|---|---|
| 0 | **Resolve size** | request → cheapest available SKU | GPU name → RunPod GPU id (default `a40`) |
| 1 | **Create** | Pulumi: RG, VNet, static IP, NSG (22/80/443), Ubuntu 22.04 VM; cloud-init installs Caddy; wait for SSH | Pulumi: a `pulumi_runpod.Pod` from your image (gpu, ports, env, volume); returns a proxy URL |
| 2 | **Ship** | `rsync` your dirs (see `ship`) | only if the pod exposes SSH (image runs `sshd`); else skipped |
| 3 | **Setup** | run `setup` commands once over SSH | same SSH gate as ship |
| 4 | **Start** | install a **systemd** service for `start` | the container's CMD (set at create); `start` overrides it |
| 5 | **Expose** | configure Caddy (reverse-proxy + HTTPS) and DNS | the proxy URL it already has |
| 6 | **Health** | wait for the app port, then poll the URL | poll the URL |

It returns a **`Deployment`** (name, IP/handle, SSH key, URL).

For a `vm`, Caddy gives you **automatic HTTPS** via Let's Encrypt once a domain is
configured. For a `pod`, RunPod gives you an HTTPS proxy URL of the form
`https://<pod-id>-<port>.proxy.runpod.net`.

---

## Install

Requirements:

- **Python 3.10+**
- The **Pulumi CLI** on your `PATH` (the library drives it via the Automation API) —
  see <https://www.pulumi.com/docs/install/>.
- **`rsync`**, **`ssh`**, and **`ssh-keygen`** (standard on macOS/Linux).
- For **`vm`** (Azure): an Azure account with permission to create resources.
- For **`pod`** (RunPod): a RunPod account + API key, and the extra packages:
  ```bash
  pip install pulumi-runpod runpod
  ```

Install the package (editable, from the repo root):

```bash
pip install -e .
infra-lib --help
```

> No Pulumi Cloud account is needed — state is stored locally (see
> [Files & state on disk](#files--state-on-disk)).

---

## Authenticate

Credentials live in `~/.infra-lib/credentials` (mode `0600`), one section per
provider. Pick the provider you'll use.

### Azure (`vm`)

`infra-lib` talks to Azure as a **service principal** (the cloud equivalent of an API
key).

**Interactive (creates a new service principal):**

```bash
infra-lib auth azure
```

Runs the **device-code flow**: visit a URL, enter a code, pick a subscription;
`infra-lib` creates an app registration + service principal, grants it **Contributor**
on the subscription, and saves the credentials.

**Non-interactive (use an existing service principal):**

```bash
infra-lib auth azure \
  --client-id       "$ARM_CLIENT_ID" \
  --tenant-id       "$ARM_TENANT_ID" \
  --subscription-id "$ARM_SUBSCRIPTION_ID" \
  --client-secret   "$ARM_CLIENT_SECRET"     # or set ARM_CLIENT_SECRET in the env
```

**Or skip saving entirely** — if these env vars are set they take priority and nothing
is saved (cleanest for headless/agent use):

```
ARM_CLIENT_ID  ARM_CLIENT_SECRET  ARM_TENANT_ID  ARM_SUBSCRIPTION_ID
```

### RunPod (`pod`)

A single **API key** (from <https://www.runpod.io/console/user/settings>):

```bash
infra-lib auth runpod --api-key "$RUNPOD_API_KEY"
# ...or just set RUNPOD_API_KEY in the env (used directly, nothing saved)
```

The key is read by both the Pulumi provider (provisioning) and the RunPod SDK
(SSH-mapping lookups, pause/resume).

---

## Quick start

**A static site (vm)** — serve a directory over HTTP:

```bash
infra-lib deploy ./site --name mysite
# → http://<ip>
```

**With your own domain + HTTPS (vm):**

```bash
infra-lib deploy ./site --name mysite --domain mysite.example.com
# provisions, shows the IP, asks you to point DNS, then serves https://mysite.example.com
```

**A runnable app from `infra.yml` (vm):**

```bash
infra-lib deploy            # reads ./infra.yml
```

**A GPU container (pod):**

```bash
infra-lib deploy --image ghcr.io/me/llm:latest --gpu a40 --port 11434 --name llm
# --image implies a pod (provider: runpod). → https://<pod-id>-11434.proxy.runpod.net
```

**Interactive (no flags, no config):** running bare `infra-lib deploy` in a terminal
launches a picker — provider → type → size/GPU → port → (domain or image).

**Manage anything:**

```bash
infra-lib list                       # all deployments (any provider)
infra-lib logs mysite -f             # stream service logs (vm / ssh-capable)
infra-lib connect mysite             # SSH in
infra-lib pause llm                  # stop billing, keep the disk/volume
infra-lib resume llm
infra-lib down mysite                # tear it all down
```

---

## The `infra.yml` file

When you run `infra-lib deploy` with no `--config`, it looks for `infra.yml` in the
current directory. CLI flags override file values. Use `--no-config` to ignore it.
`infra-lib deploy --config` (no path) opens an editor with a template to fill in.

The file has a **flat** form for the common single-unit case and a **nested**
`units:` form for several units. The provider is derived from `type` unless you set
`provider:`.

### Flat form — a `vm`

```yaml
name: myapp                 # deployment name (also the systemd service + stack name)
type: vm                    # vm | pod   (default: vm)
location: CentralUS         # Azure region
# provider: azure           # derived from type unless set

# --- size: pick ONE way ---
size: small                 # preset: micro, small, medium, large
# cpu: 4                    # ...or minimum specs (resolved to the cheapest matching size)
# ram: 16
# instance_type: Standard_D2s_v3   # ...or an exact Azure SKU (skips resolution)
# gpu: a100                 # ...or a GPU box (t4, a10, a100; driver auto-installed)

storage: 30                 # disk size in GB

ship:                       # directories rsync'd to the VM. SRC or SRC:DEST
  - .                       #   → /srv/files/<basename>
  - ../shared-lib:/srv/lib/shared-lib   # → an explicit destination

setup:                      # run once, in order, must each exit (install/build)
  - sudo apt install -y unzip
  - curl -fsSL https://bun.sh/install | bash
  - cd /srv/files/shared-lib && ~/.bun/bin/bun install

start: cd /srv/files/myapp && ~/.bun/bin/bun run server.ts   # long-running service (systemd)
port: 3000                  # app port to expose via reverse proxy

env:                        # environment variables (systemd Environment=)
  NODE_ENV: production      # secrets go here too

# --- domain (optional, vm only) ---
domain: myapp.example.com
domain_strategy: own        # own | cloudflare | http
proxied: false              # true if a proxy (e.g. Cloudflare) terminates TLS for you
# cloudflare_token: ...     # or set CLOUDFLARE_API_TOKEN (only for strategy: cloudflare)
```

> `start` must be a **single command string** — a service has one entry point.

### Flat form — a `pod`

A pod **boots from an image**, so `image` (or `build`) is required:

```yaml
name: llm
type: pod                   # → provider: runpod
gpu: a40                     # RunPod GPU: a40, rtxa6000, rtx6000ada, l40s, l40, a100, h100
storage: 60                  # volume size in GB
port: 11434                  # exposed via the RunPod proxy URL

image: ghcr.io/me/llm:latest # ...a prebuilt image (bring-your-image), OR:
# build: .                   # ...build the Dockerfile in this dir and push, then run it
# registry: ghcr.io/me       #    where `build` pushes (needs `docker login`)

start: python serve.py       # optional: overrides the container CMD

env:
  MODEL: llama3

# ship / setup also work on a pod *if* its image runs sshd (best-effort).
```

### Nested form (several units)

`name`/`location`/`provider` stay at the top; everything else moves under `units:`
(the older key `machines:` is still accepted), a mapping of name → config. Each unit
takes the same per-unit keys.

```yaml
name: myapp
units:
  web:
    type: vm
    size: small
    ports: [3000]
    ship: [.]
    domain: myapp.example.com
  gpu:
    type: pod
    gpu: a40
    image: ghcr.io/me/worker:latest
```

> Parsing both forms is supported, but `deploy` still provisions a **single** unit and
> refuses more than one (see the roadmap). The nested form is forward-looking.

---

## CLI reference

Global flag: `-v` / `-vv` enables diagnostic logging to **stderr** (info / debug).

### `infra-lib deploy [SOURCE] [options]`

Provision and deploy. `SOURCE` is an optional directory to ship (added to `ship`, vm).
With **no config and no unit-defining flags**, an interactive picker runs.

| Option | Description |
|---|---|
| `--name NAME` | Deployment name (default `default`). Also the systemd service & stack name. |
| `--type {vm,pod}` | Unit type. Defaults from config, or **`pod`** when `--image`/`--build` is given. |
| `--provider {azure,runpod}` | Cloud. Default: derived from `--type` (`vm→azure`, `pod→runpod`). |
| `--location LOC` | Region (default `CentralUS`; Azure-relevant). |
| `--size SIZE` | Preset: `micro`, `small`, `medium`, `large` (vm); a GPU label (pod). |
| `--instance-type SKU` | Exact size, e.g. `Standard_D2s_v3` (skips resolution). |
| `--cpu N` / `--ram N` | Minimum specs; resolved to the cheapest matching size. |
| `--gpu N\|TYPE` | Request a GPU: a count (`2`) or a type (`t4`/`a10`/`a100` on Azure; `a40`/`l40s`/… on RunPod). |
| `--image REF` | Container image to boot, e.g. `ghcr.io/me/app:latest`. **Implies a pod.** |
| `--build DIR` | Build the Dockerfile in DIR and push it, then run it. Implies a pod. |
| `--dockerfile FILE\|-` | Dockerfile path, `-` to read from stdin, or omit path to open `$EDITOR`. Implies a pod. |
| `--registry REG` | Push target for `--build`/`--dockerfile`, e.g. `ghcr.io/me` (needs `docker login`). |
| `--storage GB` | Disk/volume size (default 30). |
| `--port N` | App port to expose (Caddy reverse-proxy on vm; proxy URL on pod). |
| `--start CMD` | Long-running command (systemd on vm; container CMD on pod). |
| `--setup CMD` | A one-off command that runs once after deploy, before start. Must exit. Install runtimes and build here. |
| `--domain D` | Domain to serve on (vm). |
| `--domain-strategy S` | `own` (BYO DNS), `cloudflare` (auto DNS), `http` (no domain). |
| `--proxied` | A proxy terminates TLS (Caddy serves plain HTTP on the origin). |
| `--cloudflare-token T` | Cloudflare API token (or `CLOUDFLARE_API_TOKEN`). |
| `--ssh-key PATH` | Use a specific private key (default: generated per deployment). |
| `--config [FILE]` | Use a config file; omit the path to open an editor. |
| `--no-config` | Ignore any `infra.yml` in the current directory. |

### `infra-lib list [-n]`

List deployments across all providers (name, IP/handle, URL, SSH key). `-n` prints
names only — handy for scripting: `infra-lib down $(infra-lib list -n)`.

### `infra-lib logs NAME [-n LINES] [-f]`

Show the deployment's systemd service logs (`journalctl -u NAME`). `-n` sets the line
count (default 50); `-f` streams. *(Requires SSH — vm, or a pod with SSH.)*

### `infra-lib connect NAME [-e CMD]`

SSH into the machine. With `-e "cmd"`, run a one-off command. *(Requires SSH.)*

### `infra-lib pause NAME` / `infra-lib resume NAME`

Stop a deployment's compute while keeping its disk/volume, then bring it back.
On Azure this **deallocates** the VM (stops compute billing); on RunPod it **stops**
the pod (releases the GPU). See [Pause & resume](#pause--resume).

### `infra-lib down NAME... [--keep-history]`

Destroy one or more deployments (all their cloud resources) and remove them from the
local registry. `--keep-history` keeps the Pulumi stack history.

### `infra-lib auth {azure,runpod} [...]`

Authenticate — see [Authenticate](#authenticate).

### `infra-lib sizes [--provider P] --cpu N --ram N [--gpu N|TYPE] [--location LOC]`

List available sizes meeting the given specs, cheapest first. For `--provider runpod`
this lists the GPU catalog (offline); for Azure it queries live pricing.

---

## Choosing a size (vm) or GPU (pod)

Sizing always resolves a *request* into a concrete, available machine.

### vm (Azure) — three ways, all resolving to one SKU

1. **Preset** — a t-shirt size:

   | preset | vCPU | RAM | ~$/hr |
   |---|---|---|---|
   | `micro` | 1 | 1 GB | 0.011 |
   | `small` | 2 | 8 GB | 0.096 |
   | `medium` | 4 | 16 GB | 0.192 |
   | `large` | 8 | 32 GB | 0.384 |

2. **Minimum specs** (`--cpu`/`--ram`, or `cpu:`/`ram:`) — "at least this much"; picks the
   **cheapest available** size that satisfies it.
3. **Exact size** (`--instance-type` / `instance_type:`) — name the SKU; resolution only
   validates availability.

Pricing/size data is fetched from Azure with a 24h cache and a built-in fallback.

### pod (RunPod) — pick a GPU

On RunPod you pick a **GPU**, not a CPU SKU (vCPU/RAM come bundled). The presets *are*
the GPUs:

| label | GPU | vCPU | RAM | VRAM | ~$/hr |
|---|---|---|---|---|---|
| `a40` | NVIDIA A40 | 9 | 50 GB | 48 GB | 0.44 |
| `rtxa6000` | RTX A6000 | 9 | 50 GB | 48 GB | 0.49 |
| `rtx6000ada` | RTX 6000 Ada | 10 | 167 GB | 48 GB | 0.77 |
| `l40s` | L40S | 16 | 94 GB | 48 GB | 0.86 |
| `l40` | L40 | 8 | 94 GB | 48 GB | 0.99 |
| `a100` | A100 80GB | 8 | 117 GB | 80 GB | 1.64 |
| `h100` | H100 80GB | 16 | 188 GB | 80 GB | 2.99 |

Use `gpu: a40` (yaml) or `--gpu a40`. With no GPU specified a pod defaults to `a40`.
*(Prices are approximate; live values come from the API.)*

Under the hood: presets/specs are an **`ExpectedSpecs`** (a request); resolution turns
that — or a named size — into a concrete **`VMSpec`** (`type`, cpu, ram, gpus, price).

---

## `--dockerfile`: write a Dockerfile inline

Instead of pointing at a whole build directory with `--build`, you can write (or
pipe) a Dockerfile directly. All three forms create a temp build context and imply
a pod — equivalent to `--build <tempdir>`.

```bash
# Explicit path
infra-lib deploy --dockerfile ./Dockerfile --registry ghcr.io/me --name mypod

# Read from stdin — best for scripts and LLM agents
echo 'FROM python:3.11-slim
CMD python3 -m http.server 8000' | \
  infra-lib deploy --dockerfile - --registry ghcr.io/me --name mypod --port 8000

# Omit the path to open $EDITOR with a template (same pattern as --config)
infra-lib deploy --dockerfile --registry ghcr.io/me --name mypod
```

The stdin form (`--dockerfile -`) is the **agent-native path**: an LLM generates
the Dockerfile content as a string and pipes it in — no temp files to manage, no
local directory needed. It requires `--registry` and a prior `docker login` to that
registry, same as `--build`.

---

## Shipping files (`ship`)

`ship` is a list of local directories rsync'd to the machine. Each entry is either a
**source** or a **`SRC:DEST`** mapping:

```yaml
ship:
  - .                                  # → /srv/files/<basename> (default)
  - ./models:/workspace/models         # → contents land at /workspace/models
  - ../bin:~/local/bin                 # ~ expands to the login user's home
```

- **Default destination** (no `:DEST`) is `/srv/files/<basename>`.
- **`~`** is expanded by `infra-lib` to the unit's home (`/home/azureuser` on Azure,
  `/root` on a pod) — not left to the remote shell.
- Missing destination directories are **created** (`mkdir -p`) before transfer.
- Trailing slashes are normalized so the source's **contents land *at* the
  destination** (not nested under it).
- `.gitignore` is respected (rsync `--filter=:- .gitignore`).
- The following are **always excluded**, regardless of `.gitignore`:

  | Category | Excluded |
  |---|---|
  | Version control | `.git` |
  | Python | `__pycache__`, `.venv`, `venv`, `*.pyc` |
  | Node / JS | `node_modules`, `.next`, `.nuxt`, `.svelte-kit` |
  | Build output | `dist`, `out`, `build`, `target` |
  | Secrets | `.env`, `.env.*` |

  So shipping a Next.js or Node project is safe — `node_modules` (often hundreds of
  MB) and `.next` are never transferred.

`ship` runs wherever SSH is available — always on a vm; on a pod only when its image
runs `sshd` (best-effort; otherwise it's skipped with a warning).

---

## `setup` vs `start`

These look similar but are fundamentally different:

| | `setup:` | `start:` |
|---|---|---|
| Purpose | install / build | run the long-lived server |
| Lifetime | each command **runs once and must exit** | runs **forever**, supervised |
| Mechanism | sequential SSH commands | **systemd** (vm) / container **CMD** (pod) |
| On crash | n/a | `Restart=always` (vm) |
| On reboot | n/a | starts on boot (vm) |
| Logs | streamed during deploy | `infra-lib logs NAME` (vm journal) |

Put "prepare the machine" steps in `setup` (they must terminate). Put "the server" in
`start`. `start` runs after `setup`.

---

## GPUs

Two ways to get a GPU, depending on the type:

- **pod (RunPod)** — the native path. `gpu: a40` (or any label above). The image runs
  directly on the GPU; nothing to install. Cheap and fast to spin up for bursts.
- **vm (Azure)** — `gpu: a100` (types `t4`, `a10`, `a100`). `infra-lib` selects a
  GPU SKU and attaches Azure's **NVIDIA GPU Driver extension** so CUDA is ready before
  your `setup`/`start` run. Azure GPU families require a **quota increase** per family,
  per region (often gated for new/startup subscriptions) —
  <https://portal.azure.com> → Quotas.

For "spin up a GPU, use it, tear it down" workflows, prefer a `pod` and
[pause/resume](#pause--resume) to control cost.

---

## Domains & HTTPS

Domains apply to **`vm`** units. Set `--domain` (and `--domain-strategy`) or the
`domain:` keys.

- **`own`** (BYO DNS, default when a domain is given): after provisioning, `infra-lib`
  shows the IP and asks you to point an **A record** at it. Caddy then obtains a
  Let's Encrypt cert on first request → **automatic HTTPS**.
- **`cloudflare`**: DNS created automatically via the Cloudflare API (needs
  `--cloudflare-token` or `CLOUDFLARE_API_TOKEN`).
- **`http`**: no domain; serves plain HTTP on `http://<ip>`.
- **`proxied: true`**: a proxy (e.g. Cloudflare's orange-cloud) terminates TLS; Caddy
  serves plain HTTP on the origin.

With a `port`, Caddy reverse-proxies the domain to your app; without one, it serves the
shipped files (`/srv/files`) as a static site.

A **`pod`** doesn't take a domain — RunPod returns a proxy URL
(`https://<pod-id>-<port>.proxy.runpod.net`) automatically.

> **Non-interactive note:** a *bring-your-own* domain needs a human to set DNS
> mid-deploy. For fully-automated deploys, prefer `cloudflare`.

---

## Pause & resume

```bash
infra-lib pause NAME     # stop compute, keep disk/volume
infra-lib resume NAME    # bring it back
```

- **vm (Azure):** **deallocates** the VM — compute billing stops, the OS disk is kept,
  the static IP is preserved. Ideal for an expensive box you only use intermittently.
- **pod (RunPod):** **stops** the pod — the GPU is released; the volume persists.

Management commands route to the right provider automatically (the deployment
registry records which provider owns each name).

---

## Python API

The public API is **silent by default** (no console output), making it safe for
scripts and the planned MCP server.

```python
import infra_lib
from infra_lib import Infrastructure, Unit, ShipItem, ExpectedSpecs, VMSpec, Disk

# A vm
infra = Infrastructure(
    name="myapp",
    location="CentralUS",
    provider="azure",
    units=[
        Unit(
            type="vm",
            hardware=ExpectedSpecs(cpu=2, ram_gb=8),   # or VMSpec(type="Standard_D2s_v3")
            disk=Disk(size_gb=30),
            ship=[ShipItem("./app"), ShipItem("./models", "/workspace/models")],
            setup=["curl -fsSL https://bun.sh/install | bash"],
            start="cd /srv/files/app && ~/.bun/bin/bun run server.ts",
            ports=[3000],
            env={"NODE_ENV": "production"},
            domain=infra_lib.BYODomain("myapp.example.com"),
        )
    ],
)
d = infra_lib.deploy(infra)
print(d.url, d.ip, d.ssh_command)

# A pod
gpu = Infrastructure(
    name="llm", provider="runpod",
    units=[Unit(type="pod", hardware=ExpectedSpecs(gpu=1, gpu_type="a40"),
                image="ghcr.io/me/llm:latest", ports=[11434], env={"MODEL": "llama3"})],
)
print(infra_lib.deploy(gpu).url)
```

Management functions:

```python
infra_lib.list_deployments()               # -> list[Deployment]
infra_lib.get("myapp")                       # -> Deployment | None
infra_lib.logs("myapp", lines=100)           # -> str (journalctl; SSH-capable units)
infra_lib.run("myapp", "uptime")             # -> str (one-off SSH command)
infra_lib.connect("myapp")                   # -> "ssh -i … azureuser@…"
infra_lib.down("myapp")                       # destroy + purge
```

*(`pause`/`resume` are available via `infra_lib.pipeline.pause/resume`.)*

### Models

- **`Infrastructure`** — what you deploy: `name`, `location`, `provider`, `units: [Unit]`.
- **`Unit`** — one unit: `type` (`"vm"`/`"pod"`), `hardware` (`ExpectedSpecs`|`VMSpec`),
  `disk`, `ship: [ShipItem]`, `setup`, `start`, `ports`, `env`, `domain`, and (pod)
  `image`/`build`/`registry`.
- **`ShipItem(src, dest=None)`** — one `ship` entry (dest `None` → default location).
- **`ExpectedSpecs(cpu, ram_gb, gpu, gpu_type)`** — a size *request*.
- **`VMSpec(type, cpu, ram_gb, gpus, price_per_hour)`** — a concrete, resolved size.
- **`Endpoint(host, user, ssh_port, sudo, has_ssh, ssh_key, url, handle, home)`** — how a
  provider's `create()` says "here's the running machine and how to reach it"; the
  pipeline keys ship/setup off `has_ssh`.
- **`Disk(size_gb, type)`** — storage.
- **`Deployment(name, ip, ssh_key, user, services)`** — the live result, with `.url` and
  `.ssh_command` helpers.
- **`Domain` / `BYODomain` / `CloudflareDomain`**, and `build_domain(...)`.

---

## What lands on the machine

**vm (Azure):**

- **OS:** Ubuntu 22.04 LTS, admin user `azureuser`, SSH key-only login.
- **Web server:** Caddy (installed by cloud-init), serving from `/srv/files`.
- **Your code:** rsync'd to `/srv/files/<dir>` or your `ship` destinations.
- **Your service** (if `start`): a systemd unit named after the deployment,
  `Restart=always`, started on boot, logs in the journal, with your `env`.
- **Firewall (NSG):** inbound 22 (SSH), 80 (HTTP), 443 (HTTPS).

**pod (RunPod):**

- **Your image**, running as `root`, with your `env`, the GPU attached, your `port`
  exposed via the proxy URL, and a persistent volume.
- **SSH** only if the image runs `sshd` (then `infra-lib` injects your public key so
  ship/setup/connect work).

---

## Architecture

Layered, with a strict dependency direction:

```
cli/ ──►  pipeline  ──►  core/ + providers/  ──►  progress
                                                    ▲
            everything may import progress ─────────┘
```

- **`core/`** and **`providers/`** never import `cli/`.
- **`progress`** is the only cross-cutting dependency and imports nobody.
- **`pipeline.deploy()` is one flow.** It runs create → ship → setup → start → expose →
  health for every unit. The **provider** implements the substrate-specific steps —
  `create()` (returns an `Endpoint`), `start()` (systemd / no-op), `expose()` (Caddy+DNS
  / proxy URL); **ship** and **setup** are shared SSH code the pipeline runs when the
  Endpoint has SSH. A provider declares which `type` it realizes (`unit_type`) and a
  deploy is gated on the unit's type matching.

```
infra_lib/
├── __init__.py          public API
├── models.py            Infrastructure, Unit, ShipItem, Endpoint, ExpectedSpecs, VMSpec, Disk, Deployment
├── config.py            infra.yml (flat or nested) → Infrastructure; type → provider
├── progress.py          Reporter (silent by default) + module-level shims
├── pipeline.py          deploy / get / list_deployments / run / logs / connect / down / pause / resume
├── core/
│   ├── keys.py          SSH keypair generation
│   ├── registry.py      deployment registry (~/.infra-lib/deployments/<name>.json)
│   ├── container.py     build_and_push (docker build + push for `build:`)
│   ├── domain.py        Domain / BYODomain / CloudflareDomain, build_domain, Caddyfiles
│   ├── health.py        wait_for_port / wait_for_url
│   └── transfer.py      SSH/SFTP/rsync (ship SRC:DEST), run_setup, start_service, ssh_exec
├── providers/
│   ├── __init__.py      registry: get_provider(name) / provider_names()  → azure, runpod
│   ├── base.py          the Provider interface (create/start/expose/destroy/pause/resume)
│   ├── azure/           AzureProvider (vm): provision.py (Pulumi), sizes.py, auth.py
│   └── runpod/          RunPodProvider (pod): provision.py (Pulumi runpod), sizes.py, auth.py
└── cli/
    ├── main.py          argparse + config merging
    ├── reporter.py      ConsoleReporter (rich spinners, prompts)
    └── tui.py           interactive builder (provider → type → size → port → specifics)
```

The same `pipeline.deploy()` powers both the CLI and the API; only the CLI installs an
interactive **Reporter**. To add a cloud: implement `Provider` in a new
`providers/<name>/` and register it in `providers/__init__.py`.

---

## Output, logging & verbosity

Two separate channels, by design:

- **Reporter** (`progress`) — the *user-facing narrative*: steps, spinners, prompts, the
  final URL. Silent by default; the CLI swaps in a rich `ConsoleReporter`.
- **`logging`** — *diagnostics*: the `infra_lib` logger is silent by default
  (`NullHandler`). The CLI's `-v`/`-vv` attaches a **stderr** handler — never stdout, so
  it stays safe for protocol use (MCP).

```bash
infra-lib -v  deploy ./site          # info-level diagnostics
infra-lib -vv deploy ./site          # debug (SSH commands, API errors, size resolution)
```

---

## Files & state on disk

| Path | What |
|---|---|
| `~/.pulumi/stacks/infra-lib/` | Pulumi state for **vm** deployments. **Back this up.** |
| `~/.pulumi/stacks/infra-lib-runpod/` | Pulumi state for **pod** deployments. |
| `~/.infra-lib/credentials` | Saved provider credentials — Azure SP + RunPod key (`0600`). |
| `~/.infra-lib/deployments/<name>.json` | Registry: which provider/handle owns each deployment. |
| `~/.infra-lib/keys/<name>_id_rsa` | SSH keypair generated per deployment. |
| `~/.infra-lib/cache/prices_<region>.json` | 24h Azure VM price cache. |

> If you lose the Pulumi state, `infra-lib down` can no longer cleanly destroy a
> deployment — you'd delete the resources in the provider's console by hand. A durable
> (remote) state backend is on the roadmap.

---

## Examples

### Static one-file site on a domain (vm)

```bash
infra-lib deploy ./site --name at1 --domain at1.example.com --size micro
# point at1.example.com A → <printed IP>, then it serves https://at1.example.com
```

### Bun app with a shipped library, a service, and a reverse-proxied domain (vm)

`infra.yml`:

```yaml
name: hub
type: vm
size: small
ship:
  - .
  - ../agents-lib:/srv/lib/agents-lib
setup:
  - sudo apt install -y unzip
  - curl -fsSL https://bun.sh/install | bash
  - cd /srv/lib/agents-lib && ~/.bun/bin/bun install && ~/.bun/bin/bun link
start: cd /srv/files/hub && ~/.bun/bin/agents start-hub
port: 3409
domain: hub.example.com
```

```bash
infra-lib deploy            # provisions, ships, builds, starts the service, serves https
infra-lib logs hub -f       # watch the service
```

### An LLM on a GPU (pod)

```yaml
name: ollama
type: pod
gpu: a40
storage: 80
port: 11434
image: ollama/ollama:latest
env:
  OLLAMA_HOST: 0.0.0.0
```

```bash
infra-lib deploy
# → https://<pod-id>-11434.proxy.runpod.net
infra-lib pause ollama       # done for now — release the GPU
infra-lib resume ollama      # back to it later
```

---

## Troubleshooting

- **`No Azure credentials found`** → run `infra-lib auth azure` (or set the `ARM_*` env vars).
- **`No RunPod API key found`** → `infra-lib auth runpod --api-key …` (or set `RUNPOD_API_KEY`).
- **Auth fails with `Authorization_RequestDenied`** (Azure) → your tenant won't let you
  create app registrations or assign roles; use an existing service principal
  (`infra-lib auth azure --client-id …`). Run with `-vv` for the full error.
- **`No size in <region> matches …`** → specs can't be satisfied there; lower them, pick
  another region, or use `--instance-type`.
- **Azure GPU deploy rejected for quota** → GPU families start at 0 vCPUs; request a
  quota increase (per family, per region) at the Azure Quotas blade. Or use a `pod`.
- **`A 'pod' unit boots from an image …`** → a pod needs `image:` (or `build:`). Use
  `type: vm` to ship files and run a process instead.
- **`The azure provider realizes 'vm' units, not 'pod'`** → your provider and type
  disagree. Drop `provider:` to let it derive (`vm→azure`, `pod→runpod`), or fix one.
- **App not reachable after deploy** → check the service: `infra-lib logs <name>`. A
  common cause is binding to `localhost` only — bind to `0.0.0.0`.
- **HTTPS not issuing** (vm) → DNS must point at the IP *before* the first request so
  Caddy can complete the ACME challenge. Verify the A record resolves.
- **Deploy stuck on a setup step** → a `setup` command must **exit**; a long-running
  server belongs in `start`.
- **Pod ship/setup skipped** → the pod's image must run `sshd` for files to land; bake
  them into the image instead, or use one that supports SSH.

---

## Roadmap & limitations

Current limitations:

- **RunPod (`pod`) is live-untested.** The Pulumi + SDK code is written against the
  documented APIs but hasn't run against a real account yet. Verify the Pod outputs,
  the SSH port mapping (it may need polling after create), and `stop_pod`/`resume_pod`
  on first real use. Needs `pip install pulumi-runpod runpod` + an API key.
- **Single unit per deployment.** The model and `infra.yml` (flat *and* nested) support
  several units, but `deploy` provisions one and refuses more (fails loud).
- **One port / one service** is wired through end-to-end.
- **No container-on-VM yet.** A `vm` runs a *process*; to run a container on a vm you'd
  put `docker run …` in `setup`. (A first-class container-on-vm path isn't wired.)
- **Public images only.** `build:` pushes via your ambient `docker login`; pulling a
  *private* image into a pod (a `pull_secret`) isn't wired yet.

Planned (see [`todo.md`](./todo.md)):

- **MCP server** over the silent API, with guardrails (deployment caps, name validation,
  scoped service principals).
- Efficient single-deployment lookup, multi-unit deploys, reliable status, model
  serialization, multiple ports, durable Pulumi state, and private-image pull.
