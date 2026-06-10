- [ ] currently all copied directories go to `/srv/files`. Allow to put them in a specific location, maybe with a port-like sintax, like `root/dir:absolute/path/to/dir. Note that the first (what to copy) is relative, but the second (destination, where to copy) is absolute

# infra-lib — pending work

This file is the backlog. Each item is written to be actionable by someone (or an
agent) starting cold in this repo. Read the **Orientation** first, then pick an item.

---

## Orientation

**What it is.** `infra-lib` makes deploying to a cloud dead simple: describe your
infrastructure → get back a running URL. Today it provisions an Azure VM with
Pulumi, installs Caddy (auto-HTTPS), rsyncs your code, runs setup commands, and
starts a long-running service via systemd.

**Two ways in, one core:**
- **CLI** — `infra_lib/cli/main.py` (argparse). Installs an interactive reporter.
- **Library / future MCP** — `import infra_lib`; the public API is silent by default.

**Layering (strict — keep it this way):**
```
cli/ ──► pipeline ──► core/ + providers/ ──► progress
                                               ▲
         everything may import progress ───────┘
```
- `core/` and `providers/` must **never** import `cli/`.
- `progress` is the only cross-cutting dependency; it imports nobody.
- The Reporter (`progress.py`) is silent by default; `cli/main.py` is the only
  place that calls `progress.set_reporter(...)`. This is what keeps the API
  stdio-safe for MCP.

**The model (`infra_lib/models.py`):**
```
Infrastructure (input)        deploy(infra) -> Deployment (output)
├── name, location, provider
└── machines: [Machine]
        ├── name              (optional; identifies the machine)
        ├── hardware: VMSpec   (cpu, ram)
        ├── disk:     Disk     (size_gb, type)
        ├── ship / setup / start / ports
        └── domain:   Domain | None
```
`infra.yml` accepts both a **flat** one-machine form (maps onto `machines[0]`)
and a **nested** `machines:` form (name -> config), via `infra_lib/config.py`.

**Providers.** `pipeline.py`/CLI never import a cloud module directly — they go
through `providers.get_provider(name) -> Provider` (see `providers/base.py` for
the interface). To add a cloud, implement `Provider` and register it in
`providers/__init__.py`. The provider owns its cloud-specific vocabulary
(`admin_user`, `size_term`, `presets`); `core/` takes `user` as a parameter so it
stays provider-agnostic.

**Key files:**
- `pipeline.py` — `deploy / get / list_deployments / run / logs / connect / down`
- `providers/base.py` — the `Provider` interface; `providers/__init__.py` — registry
- `core/transfer.py` — SSH/SFTP/rsync, `run_setup`, `start_service`, `ssh_exec`, `open_ssh`
- `core/health.py` — `wait_for_port`, `wait_for_url`
- `core/domain.py` — `Domain`/`BYODomain`/`CloudflareDomain`, `build_domain`, Caddyfile
- `providers/azure/{provider,auth,provision,sizes}.py` — Azure impl (Pulumi, pricing, presets)

**Sanity check before/after any change:** `python3 -m compileall -q infra_lib && python3 -c "import infra_lib"`

---

## 1. MCP server  *(highest priority — this is the next major feature)*

**Goal.** Expose the library over MCP so an agent can deploy/test/manage infra.
The API was deliberately built silent-by-default for exactly this.

**Where it goes.** A new top layer, sibling to `cli/`: `infra_lib/mcp/server.py`.
It imports `infra_lib` like the CLI does, but speaks MCP over stdio. It must
**not** call `progress.set_reporter` (stay silent — stdout is the protocol channel).

**Tools to expose (one per public verb):**
- `deploy(infra)` — takes an Infrastructure-shaped JSON object → returns Deployment JSON.
- `list_deployments()` → list of Deployment JSON.
- `get(name)`, `logs(name, lines)`, `run(name, command)`, `connect(name)`, `down(name)`.

**Prerequisites / coupled work:**
- Needs **item 5** (model `to_dict`/`from_dict`) so tool args/results serialize cleanly.
- Decide the transport lib (e.g. the official `mcp` Python SDK) and add to deps in
  `pyproject.toml`; add a `[project.scripts]` entry like `infra-lib-mcp = "infra_lib.mcp.server:main"`.

**Guardrails (the user explicitly wants these):**
- (a) Cap runaway resource creation — e.g. refuse `deploy` when active deployment
  count exceeds a configurable max.
- (b) Never touch infra the lib didn't create — `down`/`destroy` already only act on
  infra-lib's own Pulumi stacks (`project_name="infra-lib"`); keep it that way and
  add a guard that the named stack exists before destroying.
- (d) **Scoped service principal for agent use.** `auth_azure()` grants the SP
  **Contributor on the whole subscription** — fine for a human, too broad for an
  autonomous agent. Add a mode that scopes the role assignment to a single resource
  group (or a custom role limited to compute/network/disk) so an agent's blast radius is
  bounded at the cloud level, complementing the in-process guardrails. The role
  assignment is in `providers/azure/auth.py` (the `roleAssignments` PUT); scoping means
  changing the assignment scope from `/subscriptions/{id}` to a resource-group path.
- (c) **Sanitize/validate the deployment `name`.** It is interpolated unescaped into
  shell commands and file paths — e.g. `core/transfer.py:start_service` builds
  `/etc/systemd/system/{name}.service` and `systemctl enable {name}`. Today `name` comes
  from trusted config/CLI, but an agent passing `x; rm -rf /` would inject. Add a single
  validator (e.g. `^[a-z0-9][a-z0-9-]{0,40}$`) at the deploy/`down`/`run`/`logs` boundary
  (likely in `pipeline.py` and/or on `Infrastructure`), reused by CLI and MCP.

---

## 2. Efficient single-deployment lookup

**Problem.** `get(name)` in `pipeline.py` calls the provider's `list_deployments()`,
which enumerates **every** Pulumi stack and reads each one's outputs, then filters by
name. So `infra-lib logs x`, `run`, `connect`, `get` all pay the cost of reading every
deployment's state.

**Fix.** Add `get_deployment(name) -> dict | None` to
`infra_lib/providers/azure/provision.py` that `select_stack`s the single stack by name
and reads only its outputs (return None / handle the "stack not found" case). Then make
`pipeline.get()` call that instead of `_raw_list()`.

---

## 3. Multi-machine deployment

**Status.** The model supports `machines: [Machine, ...]` (each with an optional
`name`), and `config.py` now **parses** both the flat and nested `machines:` forms.
But `pipeline.deploy()` still handles only `machines[0]` and **raises
`NotImplementedError` if given more** (intentional fail-loud guard, see `deploy()` top).
So provisioning N machines is the remaining work.

**Fix.** Implement provisioning N machines. Considerations:
- `provider/azure/provision.py` `_make_infrastructure` builds one VM + NIC + public IP.
  Generalize to create one set per machine (unique Pulumi resource names per machine).
- `Deployment` output needs to represent multiple machines/IPs (today it's single
  `ip`/`ssh_key`). Probably `Deployment.machines: [MachineState]` or similar.
- Each machine's `domain`/`ports`/`ship`/`setup`/`start` apply to that machine.
- Config parsing already done (`config._parse_machines`); flat form still works.
- Remove the guard once done.

---

## 4. Reliable deployment status

**Context.** A `status` field was removed from `Deployment` because it was unreliable
(`get`/`list` always reported `"unknown"`; only the `deploy()` return was meaningful).

**Fix.** Reintroduce status only if it's derived from a real check: e.g. on `get`/`list`,
probe the IP (TCP connect to 80/443 or the app port) and/or `systemctl is-active <name>`
via `ssh_exec`. Consider making it lazy/optional so `list` stays fast (don't SSH to every
box by default — maybe a `--status` flag on `list`, or a separate `status(name)` verb).
Use a small enum/Literal, not a free string.

---

## 5. Model serialization (for MCP / --json)

**Why.** MCP (item 1) needs Infrastructure/Deployment ↔ JSON.

**Status.** The *nested `machines:` infra.yml* part of this item is **done**
(`config.py` parses flat and nested forms). The remaining piece is JSON
serialization of the models.

**Fix.** Add `to_dict()` / `from_dict()` (or use `dataclasses.asdict` + a hand-written
`from_dict`) on `Infrastructure`, `Machine`, `Deployment`. `Domain` needs custom handling
(it's a class hierarchy with a secret token — decide what to serialize; likely name +
strategy + proxied, never the token). Keep serialization with the models, not scattered
in tool handlers.

---

## 6. Multiple ports / services per machine

**Status.** `Machine.ports` is a list, but `pipeline.deploy()` only uses `ports[0]` for
the Caddy `reverse_proxy`, and builds a single `Service`.

**Fix.** Support multiple exposed ports → multiple Caddy `reverse_proxy` blocks (likely
path- or subdomain-based routing) and multiple `Service` entries on the `Deployment`.
Touches `core/domain.py` (`caddyfile`/`default_caddyfile`) and the `services=[...]`
construction in `deploy()`.

---

## 7. Durable Pulumi state backend

**Context.** State lives machine-local in `~/.pulumi/stacks/` (one JSON per stack with
all resource IDs). It's what `down`/`destroy` rely on to tear a deployment down cleanly.
If that directory is lost, you can no longer cleanly destroy through Pulumi — you'd have
to delete resources by hand in the Azure portal.

**Fix / decision.** Consider defaulting state to a durable remote backend — an Azure Blob
Storage container the library provisions automatically on first use. Benefits: state
survives a machine switch, can be shared across people, no Pulumi Cloud account needed.
Cost: a one-time setup step. See `providers/azure/provision.py` (the
`auto.LocalWorkspaceOptions` / `PULUMI_CONFIG_PASSPHRASE` setup) for where the backend is
configured.

---

## 8. Smaller cleanups

- **Document the headless auth paths.** Two ways to auth without the device-code flow,
  for agent/CI use: (1) set `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID` /
  `ARM_SUBSCRIPTION_ID` env vars — `load_azure_credentials()` already prefers these and
  saves nothing; (2) `infra-lib auth azure --client-id ... --tenant-id ...
  --subscription-id ...` (secret via flag or `ARM_CLIENT_SECRET`) to persist an existing
  SP via `save_azure_credentials()`. Worth a README/MCP-docs section.

- **Programmatic BYODomain + manual DNS races.** In the silent API path, `need_dns()`
  just proceeds (can't pause an agent), so a `BYODomain` with manual DNS will likely fail
  the health check. Document that programmatic/MCP use should use `CloudflareDomain`
  (`auto_dns=True`), or detect a manual-DNS BYODomain in the API path and skip/adjust the
  health check. See `progress.Reporter.need_dns` and `pipeline.deploy`.
- **`_to_deployment` loses the real port.** Listed/looked-up deployments hardcode
  `port=80` because only `public_ip`/`url` are persisted in Pulumi outputs. If the app
  port matters after the fact (e.g. for `status`), persist it as a stack output in
  `provision._make_infrastructure` and read it back.
- **AWS / GCP providers.** The `Provider` interface and registry now exist
  (`providers/base.py`, `providers/__init__.py`); `azure/` is the only impl. Adding a
  cloud = a new `providers/<name>/` with a `Provider` subclass (provision/destroy/
  list_deployments via that cloud's IaC + auth + sizing) registered in `_BUILTIN`. The
  Azure impl scopes credentials to the whole subscription and opens 22/80/443 — replicate
  the equivalents (and see the SSH-to-world note below) per cloud. *(Provider interface
  itself: done. AWS/GCP impls: not started — only add when actually needed.)*
- **Management ops don't know their provider.** `get/list/run/logs/connect/down` only have
  a deployment name, so `pipeline._management_provider()` defaults to the built-in
  provider. Once a second cloud exists, persist the provider per deployment (e.g. as a
  Pulumi stack tag/output) and look it up there. Fine while Azure is the only provider.
- **NSG opens SSH (22) to the world.** `providers/azure/provision.py` `_make_infrastructure`
  allows 22/80/443 from `*` (0.0.0.0/0). 80/443 must be public, but SSH being world-open
  invites scanning/brute-force (mitigated only by key-only auth). Consider restricting 22
  to the deployer's current public IP, or making the SSH source range configurable.
- **`AutoAddPolicy` accepts any SSH host key** (`core/transfer.py:_connect`). Pragmatic
  for freshly-minted ephemeral VMs, but a MITM tradeoff. If hardening later, capture the
  host key at provision time and pin it.
- **Auth is interactive and not MCP-callable.** `providers/azure/auth.py` does direct
  `print()`/`input()` (subscription picker) and relies on the device-code flow — an agent
  can't drive it, and it's I/O in a `providers/` module (layering smell). This is fine as
  long as the model is "a human runs `infra-lib auth azure` once; the agent reuses the
  saved creds" — document that for the MCP layer. If auth ever needs to be programmatic,
  route its prompts through a Reporter callback and accept a pre-chosen subscription id.
- **Redundant readiness waits.** Both `transfer()` and `run_setup()` call
  `_wait_for_ssh` + `_wait_for_cloud_init` at the top, so a single deploy prints
  "Cloud-init complete" twice. Harmless, just noisy — could hoist the wait to the start of
  `pipeline.deploy` once and skip it in the steps.
