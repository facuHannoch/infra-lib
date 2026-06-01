# infra-lib

**Point at a directory, get back a URL.** `infra-lib` makes deploying to the cloud
dead simple: describe what you want, and it provisions a VM, ships your code, wires
up a web server with automatic HTTPS, and hands you back a running deployment.

It is usable three ways from the same core:

- a **CLI** (`infra-lib deploy …`) for humans,
- a **Python API** (`import infra_lib`) for scripts, and
- (planned) an **MCP server** so agents can deploy/manage infrastructure.

Today it ships an **Azure** provider; clouds sit behind a `Provider` interface, so
others can slot in without touching the pipeline.

---

## Table of contents

- [How it works](#how-it-works)
- [Install](#install)
- [Authenticate](#authenticate)
- [Quick start](#quick-start)
- [The `infra.yml` file](#the-infrayml-file)
- [CLI reference](#cli-reference)
- [Choosing a VM size](#choosing-a-vm-size)
- [Domains & HTTPS](#domains--https)
- [`setup` vs `start`](#setup-vs-start)
- [Python API](#python-api)
- [What lands on the VM](#what-lands-on-the-vm)
- [Architecture](#architecture)
- [Output, logging & verbosity](#output-logging--verbosity)
- [Files & state on disk](#files--state-on-disk)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Roadmap & limitations](#roadmap--limitations)

---

## How it works

A single `deploy` runs this pipeline:

1. **Resolve the size.** Your request (`small`, or `cpu/ram`, or an exact SKU) is
   resolved to a concrete, *available* VM size in the target region.
2. **Provision.** Pulumi creates a resource group, virtual network + subnet, a
   **static public IP**, a network security group (opens 22/80/443), a NIC, and an
   Ubuntu 22.04 VM. Cloud-init installs **Caddy** on first boot.
3. **Report the IP** (static, so it won't change across redeploys).
4. **Configure DNS** if a domain is set — automatically (Cloudflare) or by asking you
   to point an A record (bring-your-own domain).
5. **Ship your code** with `rsync` into `/srv/files`, and write a Caddyfile.
6. **Run setup** commands (install/build steps that run once).
7. **Start the service** as a systemd unit (your long-running server), if any.
8. **Health-check**: wait for the app port, then poll the public URL until it's live.
9. Return a **`Deployment`** (name, IP, SSH key, URL).

Caddy gives you **automatic HTTPS** via Let's Encrypt when a domain is configured.

---

## Install

Requirements:

- **Python 3.10+**
- The **Pulumi CLI** on your `PATH` (the library drives it via the Automation API) —
  see <https://www.pulumi.com/docs/install/>.
- **`rsync`**, **`ssh`**, and **`ssh-keygen`** available (standard on macOS/Linux).
- An **Azure account** with permission to create resources (and, for the interactive
  auth flow, to register an application — see below).

Install the package (editable, from the repo root):

```bash
pip install -e .
infra-lib --help
```

> No Pulumi Cloud account is needed — state is stored locally (see
> [Files & state on disk](#files--state-on-disk)).

---

## Authenticate

`infra-lib` talks to Azure as a **service principal** (the cloud equivalent of an API
key). There are two ways to set one up.

### Interactive (creates a new service principal)

```bash
infra-lib auth azure
```

This runs the **device-code flow**: you visit a URL, enter a code, pick a subscription,
and `infra-lib` creates an app registration + service principal, grants it **Contributor**
on the subscription, and saves the credentials to `~/.infra-lib/credentials`.

### Non-interactive (use an existing service principal)

For CI or agents, hand it an SP you already have:

```bash
infra-lib auth azure \
  --client-id    "$ARM_CLIENT_ID" \
  --tenant-id    "$ARM_TENANT_ID" \
  --subscription-id "$ARM_SUBSCRIPTION_ID" \
  --client-secret "$ARM_CLIENT_SECRET"      # or set ARM_CLIENT_SECRET in the env
```

The credentials are validated against Azure before being saved.

### Or skip saving entirely

If these environment variables are set, they take priority and nothing is saved:

```
ARM_CLIENT_ID  ARM_CLIENT_SECRET  ARM_TENANT_ID  ARM_SUBSCRIPTION_ID
```

This is the cleanest path for headless/agent use.

---

## Quick start

**A static site** — serve a directory over HTTP:

```bash
infra-lib deploy ./site --name mysite
# → http://<ip>
```

**With your own domain + HTTPS:**

```bash
infra-lib deploy ./site --name mysite --domain mysite.example.com
# provisions, shows the IP, asks you to point DNS, then serves https://mysite.example.com
```

**A runnable app** (e.g. a Bun server on port 3000), via [`infra.yml`](#the-infrayml-file):

```bash
infra-lib deploy            # reads ./infra.yml
```

**Manage it:**

```bash
infra-lib list                       # all deployments
infra-lib logs mysite -f             # stream service logs
infra-lib connect mysite             # SSH in
infra-lib down mysite                # tear it all down
```

---

## The `infra.yml` file

When you run `infra-lib deploy` with no `--config`, it looks for `infra.yml` in the
current directory. CLI flags override values in the file. Use `--no-config` to ignore it.

`infra-lib deploy --config` (no path) opens an editor with a template to fill in.

The file comes in two shapes: a **flat** form for the common single-machine case, and a
**nested** `machines:` form for several machines (see below). CLI flags override file values.

### Flat form (one machine)

A fully-annotated example:

```yaml
name: myapp                 # deployment name (also the systemd service + stack name)
location: CentralUS         # Azure region
provider: azure             # cloud to deploy to (default: azure)

# --- VM size: pick ONE way ---
vm: small                   # preset: micro, small, medium, large
# cpu: 4                    # ...or minimum specs (resolved to the cheapest matching size)
# ram: 16
# instance_type: Standard_D2s_v3   # ...or an exact Azure size (skips resolution)

storage: 30                 # OS disk size in GB (min 30)

ship:                       # directories rsync'd to /srv/files on the VM
  - .
  - ../shared-lib

setup:                      # run once, in order, must each exit (install/build)
  - sudo apt install -y unzip
  - curl -fsSL https://bun.sh/install | bash
  - cd /srv/files/shared-lib && ~/.bun/bin/bun install

start: cd /srv/files/myapp && ~/.bun/bin/bun run server.ts   # long-running service (systemd)
port: 3000                  # app port to expose via reverse proxy

# --- Domain (optional) ---
domain: myapp.example.com
domain_strategy: own        # own | cloudflare | http
proxied: false              # true if a proxy (e.g. Cloudflare) terminates TLS for you
# cloudflare_token: ...     # or set CLOUDFLARE_API_TOKEN (only for strategy: cloudflare)
```

> `start` must be a **single command string** — a service has one entry point. For
> multiple processes, that's multiple services (not yet supported; see the roadmap).

### Nested form (several machines)

`name`/`location`/`provider` stay at the top level; everything else moves under
`machines:`, a mapping of machine name → config. Each machine takes the same per-machine
keys as the flat form (`vm`/`cpu`/`ram`/`instance_type`, `storage`, `ports`, `ship`,
`setup`, `start`, `domain`). `port:` is accepted as an alias for `ports: [N]`.

```yaml
name: myapp
provider: azure
machines:
  web:
    vm: small
    ports: [3000]
    ship: [.]
    domain: myapp.example.com
  worker:
    cpu: 4
    ram: 16
    start: ~/.bun/bin/bun run worker
```

> Parsing both forms is supported today, but `deploy` still provisions a **single**
> machine and refuses more than one (see the roadmap). The nested form is forward-looking.

---

## CLI reference

Global flag: `-v` / `-vv` enables diagnostic logging to **stderr** (info / debug).

### `infra-lib deploy [SOURCE] [options]`

Provision and deploy. `SOURCE` is an optional directory to ship (added to `ship`).

| Option | Description |
|---|---|
| `--name NAME` | Deployment name (default `default`). Also the systemd service & stack name. |
| `--provider P` | Cloud provider (default `azure`; currently the only choice). |
| `--location LOC` | Azure region (default `CentralUS`). |
| `--vm SIZE` | Preset: `micro`, `small`, `medium`, `large`. |
| `--instance-type SKU` | Exact size, e.g. `Standard_D2s_v3` (skips resolution). |
| `--cpu N` / `--ram N` | Minimum specs; resolved to the cheapest matching size. |
| `--storage GB` | OS disk size (default 30). |
| `--port N` | App port to expose via Caddy reverse proxy. |
| `--start CMD` | Command to run as a supervised systemd service. |
| `--install CMD` | A one-off setup command (appended to `setup`). |
| `--domain D` | Domain to serve on. |
| `--domain-strategy S` | `own` (bring-your-own DNS), `cloudflare` (auto DNS), `http` (no domain). |
| `--proxied` | A proxy terminates TLS (Caddy serves plain HTTP on the origin). |
| `--cloudflare-token T` | Cloudflare API token (or `CLOUDFLARE_API_TOKEN`). |
| `--ssh-key PATH` | Use a specific private key (default: generated per deployment). |
| `--config [FILE]` | Use a config file; omit the path to open an editor. |
| `--no-config` | Ignore any `infra.yml` in the current directory. |

If no size is given (no flag, no config), the CLI shows an **interactive picker**.

### `infra-lib list [-n]`

List deployments (name, IP, URL, SSH key). `-n` prints names only — handy for scripting:

```bash
infra-lib down $(infra-lib list -n)
```

### `infra-lib logs NAME [-n LINES] [-f]`

Show the deployment's systemd service logs (`journalctl -u NAME`). `-n` sets the line
count (default 50); `-f` streams.

### `infra-lib connect NAME [-e CMD]`

SSH into the VM. With `-e "cmd"`, run a one-off command instead of an interactive shell.

### `infra-lib down NAME... [--keep-history]`

Destroy one or more deployments (all their cloud resources). By default it also purges
the local Pulumi stack history; `--keep-history` keeps it.

### `infra-lib auth azure [...]`

Authenticate — see [Authenticate](#authenticate).

### `infra-lib sizes --cpu N --ram N [--location LOC]`

List available VM sizes meeting the given specs, cheapest first.

---

## Choosing a VM size

There are **three ways** to say how big the machine should be — and they all resolve to
the same thing: a concrete, available size.

1. **Preset** — a t-shirt size:

   | preset | vCPU | RAM | ~$/hr |
   |---|---|---|---|
   | `micro` | 1 | 1 GB | 0.011 |
   | `small` | 2 | 8 GB | 0.096 |
   | `medium` | 4 | 16 GB | 0.192 |
   | `large` | 8 | 32 GB | 0.384 |

2. **Minimum specs** (`--cpu` / `--ram`, or `cpu:`/`ram:` in YAML) — "at least this much";
   resolution picks the **cheapest available** size that satisfies it.

3. **Exact size** (`--instance-type` / `instance_type:`) — name the SKU directly; resolution
   only validates it's available in the region.

Under the hood: presets and specs are an **`ExpectedSpecs`** (a request); resolution turns
that — or a named size — into a concrete **`VMSpec`** (`type`, cpu, ram, price). Availability
is part of resolution, so an unsatisfiable size fails fast (and the interactive picker
lets you choose again). Pricing/size data is fetched from Azure with a 24h cache and a
built-in fallback, so resolution still works if the pricing API is rate-limited.

---

## Domains & HTTPS

Set `--domain` (and `--domain-strategy`) or the `domain:` keys in `infra.yml`.

- **`own`** (bring-your-own DNS, the default when a domain is given): after provisioning,
  `infra-lib` shows the VM's IP and asks you to point an **A record** at it. Caddy then
  obtains a Let's Encrypt certificate on the first request → **automatic HTTPS**.
- **`cloudflare`**: DNS is created automatically via the Cloudflare API (needs
  `--cloudflare-token` or `CLOUDFLARE_API_TOKEN`).
- **`http`**: no domain; serves plain HTTP on `http://<ip>`.
- **`proxied: true`**: use when a proxy (e.g. Cloudflare's orange-cloud) terminates TLS
  in front of the VM; Caddy then serves plain HTTP on the origin.

With a `port`, Caddy **reverse-proxies** the domain to your app. Without a port, it serves
the shipped files (`/srv/files`) as a static site with a directory listing.

> **Non-interactive note:** a *bring-your-own* domain needs a human to set DNS mid-deploy.
> For fully-automated (CI/agent) deploys, prefer `cloudflare` (auto DNS). In a
> non-interactive run the DNS pause is skipped and the URL check simply polls.

---

## `setup` vs `start`

These look similar but are fundamentally different — getting them right is what makes a
deploy reliable:

| | `setup:` | `start:` |
|---|---|---|
| Purpose | install / build | run the long-lived server |
| Lifetime | each command **runs once and must exit** | runs **forever**, supervised |
| Mechanism | sequential SSH commands | a **systemd** unit |
| On crash | n/a | `Restart=always` |
| On reboot | n/a | starts on boot |
| Logs | streamed during deploy | `infra-lib logs NAME` (journal) |

Put "prepare the machine" steps in `setup` (they must terminate). Put "the server" in
`start` — systemd owns the process, so it survives disconnects/reboots and can't hang the
deploy. `start` runs after `setup`.

---

## Python API

The public API is **silent by default** (no console output), making it safe for scripts
and for the planned MCP server.

```python
import infra_lib
from infra_lib import Infrastructure, Machine, ExpectedSpecs, VMSpec, Disk

infra = Infrastructure(
    name="myapp",
    location="CentralUS",
    machines=[
        Machine(
            hardware=ExpectedSpecs(cpu=2, ram_gb=8),   # or VMSpec(type="Standard_D2s_v3")
            disk=Disk(size_gb=30),
            ship=["./app"],
            setup=["curl -fsSL https://bun.sh/install | bash"],
            start="cd /srv/files/app && ~/.bun/bin/bun run server.ts",
            ports=[3000],
            domain=infra_lib.BYODomain("myapp.example.com"),
        )
    ],
)

d = infra_lib.deploy(infra)          # resolves the size, provisions, ships, starts
print(d.url, d.ip, d.ssh_command)
```

Management functions:

```python
infra_lib.list_deployments()              # -> list[Deployment]
infra_lib.get("myapp")                     # -> Deployment | None
infra_lib.logs("myapp", lines=100)         # -> str (journalctl)
infra_lib.run("myapp", "uptime")           # -> str (one-off SSH command)
infra_lib.connect("myapp")                 # -> "ssh -i … azureuser@…"
infra_lib.down("myapp")                    # destroy + purge
```

### Models

- **`Infrastructure`** — the thing you deploy: `name`, `location`, `provider`,
  `machines: [Machine]`.
- **`Machine`** — one VM: optional `name`, `hardware` (`ExpectedSpecs` | `VMSpec`),
  `disk` (`Disk`), `ship`, `setup`, `start`, `ports`, `domain`.
- **`ExpectedSpecs(cpu, ram_gb)`** — a size *request* (resolved to a `VMSpec`).
- **`VMSpec(type, cpu, ram_gb, price_per_hour)`** — a concrete, resolved size.
- **`Disk(size_gb, type)`** — storage.
- **`Deployment(name, ip, ssh_key, user, services)`** — the live result, with `.url` and
  `.ssh_command` helpers.
- **`Domain` / `BYODomain` / `CloudflareDomain`**, and `build_domain(...)`.

> A `Machine`'s `hardware` may be given as an `ExpectedSpecs` (a request); `deploy()`
> resolves it into a concrete `VMSpec` before provisioning and stores that back.

---

## What lands on the VM

- **OS:** Ubuntu 22.04 LTS, admin user `azureuser`, SSH key-only login.
- **Web server:** Caddy (installed by cloud-init), serving from `/srv/files`.
- **Your code:** rsync'd into `/srv/files/<dir>` (respects `.gitignore`; always excludes
  `.git`, `__pycache__`, `.venv`, `venv`, `node_modules`, `.env`).
- **Your service** (if `start`): a systemd unit named after the deployment, with
  `Restart=always`, started on boot, logs in the journal.
- **Firewall (NSG):** inbound 22 (SSH), 80 (HTTP), 443 (HTTPS).

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
- **Clouds sit behind a `Provider`.** `pipeline`/`cli` reach a cloud only through
  `providers.get_provider(name)` — never by importing a cloud module. The provider owns
  its cloud-specific vocabulary (`admin_user`, `size_term`, `presets`); `core/` takes the
  SSH `user` as a parameter so it stays provider-agnostic.

```
infra_lib/
├── __init__.py          public API
├── models.py            Infrastructure, Machine, ExpectedSpecs, VMSpec, Disk, Deployment, Service
├── config.py            infra.yml (flat or nested) → Infrastructure
├── progress.py          Reporter (silent by default) + module-level shims
├── pipeline.py          deploy / get / list_deployments / run / logs / connect / down
├── core/
│   ├── keys.py          SSH keypair generation
│   ├── domain.py        Domain / BYODomain / CloudflareDomain, build_domain, Caddyfiles
│   ├── health.py        wait_for_port / wait_for_url
│   └── transfer.py      SSH/SFTP/rsync, run_setup, start_service, ssh_exec, open_ssh
├── providers/
│   ├── __init__.py      provider registry: get_provider(name) / provider_names()
│   ├── base.py          the Provider interface
│   └── azure/
│       ├── provider.py  AzureProvider (the Provider impl; facade over the modules below)
│       ├── auth.py      device-code & service-principal auth
│       ├── provision.py Pulumi program + provision / destroy / list
│       └── sizes.py     presets, pricing, resolve()
└── cli/
    ├── main.py          argparse + config merging
    ├── reporter.py      ConsoleReporter (rich spinners, prompts)
    └── tui.py           interactive size picker
```

The same `pipeline.deploy()` powers both the CLI and the API; only the CLI installs an
interactive **Reporter**. That's what keeps the API silent and reusable.

To add a cloud: implement `Provider` (`providers/base.py`) in a new `providers/<name>/`
and register it in `providers/__init__.py`.

---

## Output, logging & verbosity

Two separate channels, by design:

- **Reporter** (`progress`) — the *user-facing narrative*: steps, spinners, prompts, the
  final URL. Silent by default; the CLI swaps in a rich `ConsoleReporter`.
- **`logging`** — *diagnostics*: the `infra_lib` logger is silent by default
  (`NullHandler`). The CLI's `-v`/`-vv` attaches a **stderr** handler (info/debug) — never
  stdout, so it stays safe for protocol use (MCP).

```bash
infra-lib -v  deploy ./site          # info-level diagnostics
infra-lib -vv deploy ./site          # debug (SSH commands, API errors, size resolution)
```

---

## Files & state on disk

| Path | What |
|---|---|
| `~/.pulumi/stacks/` | Pulumi state — the record of what to destroy. **Back this up.** |
| `~/.infra-lib/credentials` | Saved Azure service-principal credentials (`0600`). |
| `~/.infra-lib/keys/<name>_id_rsa` | SSH keypair generated per deployment. |
| `~/.infra-lib/cache/prices_<region>.json` | 24h VM price cache. |

> If you lose `~/.pulumi/stacks/`, `infra-lib down` can no longer cleanly destroy a
> deployment — you'd delete the resources in the Azure portal by hand. A durable
> (remote) state backend is on the roadmap.

---

## Examples

### Static one-file site on a domain

```bash
infra-lib deploy ./site --name at1 --domain at1.example.com --vm micro
# point at1.example.com A → <printed IP>, then it serves https://at1.example.com
```

### Bun app with a shipped library, a service, and a reverse-proxied domain

`infra.yml`:

```yaml
name: hub
vm: small
ship:
  - .
  - ../agents-lib
setup:
  - sudo apt install -y unzip
  - curl -fsSL https://bun.sh/install | bash
  - cd /srv/files/agents-lib && ~/.bun/bin/bun install && ~/.bun/bin/bun link
start: cd /srv/files/hub && ~/.bun/bin/agents start-hub
port: 3409
domain: hub.example.com
domain_strategy: own
```

```bash
infra-lib deploy            # provisions, ships, builds, starts the service, serves https
infra-lib logs hub -f       # watch the service
```

---

## Troubleshooting

- **`No Azure credentials found`** → run `infra-lib auth azure` (or set the `ARM_*` env vars).
- **Auth fails with `Authorization_RequestDenied`** → your tenant won't let you create app
  registrations or assign roles; ask an admin, or use an existing service principal
  (`infra-lib auth azure --client-id …`). Run with `-vv` to see Azure's full error.
- **`No size in <region> matches …`** → the specs can't be satisfied there; lower them,
  pick another region, or use `--instance-type`.
- **App not reachable after deploy** → check the service: `infra-lib logs <name>`. A common
  cause is the app binding to `localhost` only — bind to `0.0.0.0` (Caddy proxies from
  localhost, so binding to `localhost:<port>` is fine; binding to a single external IP is
  not).
- **HTTPS not issuing** → DNS must point at the VM's IP *before* the first request so Caddy
  can complete the ACME challenge. Verify the A record resolves.
- **Deploy seems stuck on a setup step** → a `setup` command must **exit**. A long-running
  server belongs in `start`, not `setup`.

---

## Roadmap & limitations

Current limitations:

- **Single machine per deployment.** The model and `infra.yml` (flat *and* nested) support
  several machines, but `deploy` currently provisions `machines[0]` and refuses more
  (fails loud).
- **One port / one service** is wired through end-to-end.
- **Azure only.** The `Provider` interface and registry exist (selection *is* abstracted);
  Azure is the only implementation. Other clouds are unimplemented, not unsupported.

Planned (see [`todo.md`](./todo.md) for details):

- **MCP server** over the silent API, with guardrails (deployment caps, name validation,
  scoped service principals).
- Efficient single-deployment lookup, multi-machine deploys, reliable status, model
  serialization, multiple ports, and a durable Pulumi state backend.
