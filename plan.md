# Plan: unit + type, one flow, RunPod on Pulumi

The redesign we converged on. Replaces the "workload axis / two deploy paths"
model that's currently half-built (image-infers-container, `provider.workloads`,
`_deploy_process` vs `_deploy_container`, RunPod-via-SDK).

## The model (one paragraph)

There is one first-class thing: a **unit**. A unit has a `type` (`vm` | `pod`)
— a *realization detail*, not the subject. The deploy pipeline runs the **same
ordered steps for every unit**; a step only no-ops when the substrate genuinely
can't do it (e.g. a pod with no SSH can't be shipped to), never by type-fiat. We
do **not** fold/collapse/reorder steps per type. `vm` is not a first-class object;
the unit is, and `vm`/`pod` is an attribute on it.

Why one `unit` + `type` and not two `vm:`/`pod:` objects: the field sets overlap
~80% (resources, `start`, `env`, `domain`, `setup` are shared; only `image` is
pod-specific and `ship` semantics differ). Two objects would duplicate the shared
surface and drift. `type` as a **discriminated (tagged) union** gives per-type
validation (e.g. `image` required when `type: pod`) without splitting the schema.

## Config schema

```yaml
name: at1
unit:                  # flat top-level fields are also accepted (one-unit form)
  type: pod            # vm | pod   (discriminator). vm -> azure, pod -> runpod by default.
  # provider: runpod   # only to override the default for the type

  # resources (shared)
  cpu: 4               # or instance_type: <exact sku>
  ram: 16
  gpu: a40             # type name | count | {type,count}
  storage: 60
  ports: [11434]

  setup:               # list of commands, run once, must exit. Runs wherever SSH is available.
    - pip install -r requirements.txt
  ship:                # rsync. Runs wherever SSH is available. `src:dest` supported (see below).
    - ./app:/workspace/app
  start: python serve.py   # the single long-running command. vm: systemd; pod: container CMD.

  image: ghcr.io/me/agents:latest   # pod: required (it boots from this). vm: ignored for now.
  # build: .           # optional convenience: build this Dockerfile + push, use result as image
  # registry: ghcr.io/me

  env: { MODEL: llama3 }   # both. systemd Environment= on vm, pod env at create. Secrets go here.
  domain: at1.example.com  # vm: Caddy+DNS. pod: ignored (auto proxy URL).
```

Decided:
- Drop the `vm: micro` size shorthand (it collides with `type: vm`). Keep
  `cpu`/`ram`/`gpu`/`instance_type`; optionally a `size:` preset later.
- `image` on a `vm` is ignored for now (docker-on-a-vm is just `setup` commands;
  build the auto-docker convenience only if ever wanted).

## The one pipeline

```
1. create   -> provider.create(unit) -> Endpoint | None
              vm:  Pulumi VM. pod: Pulumi Pod (consumes image/env/start/ports/volume here).
2. ship     -> shared rsync, IF endpoint has SSH. pod gets SSH when its image runs sshd
              and we inject PUBLIC_KEY; otherwise this step no-ops.
3. setup    -> shared SSH command list, IF endpoint has SSH. Same gate as ship.
4. start    -> provider-specific: vm installs systemd service; pod no-ops (CMD already
              set at create). Single command either way.
5. domain   -> vm: Caddy + DNS. pod: no-op (RunPod returns a proxy URL).
6. health   -> shared: wait_for_port / wait_for_url.
```

Order is fixed for everyone: **create → ship → setup → start → domain → health**
(this already matches today's `_deploy_process`: ship before setup before start).
`setup` always runs *after* `ship` — file-dependent setup needs the files present,
and pure-env setup doesn't care; there's no benefit to before-ship.

`provider.create()` returns an `Endpoint` (host/user/ssh_port/sudo) or `None`/an
endpoint-without-SSH. The pipeline keys ship/setup off "does this endpoint expose
SSH", not off `type`. That's the whole no-op mechanism.

## `ship` gains `src:dest`

`./app:/workspace/app` -> rsync `./app` to land *at* `/workspace/app`. When no
`:dest` is given, keep today's default (`/srv/files/<basename>`). Handle the three
edge cases:
- **`~` expansion**: expand ourselves to the unit's known home (`/root` for a pod,
  `/home/azureuser` for Azure) — don't rely on remote-shell expansion under rsync.
- **dir creation**: `ssh mkdir -p <dest-parent>` before each rsync (don't assume
  remote rsync >=3.2.3 for `--mkpath`).
- **trailing-slash semantics**: normalize to `src/ -> dest/` so the source lands
  *at* dest, never nested as `dest/<basename>`.
- parse by first `:` (`partition`); a source path containing `:` is unsupported
  (document it, don't build an escape syntax).

## File-by-file

- **models.py**
  - `Machine` -> `Unit`. Add `type: str = "vm"`. Drop `is_container` / `build`
    inference as the path selector (keep `image`/`build`/`registry`/`env` as
    fields). `ship` entries carry an optional dest (parse into `(src, dest)`).
  - Keep `Endpoint` (already added); `create()` returns it.
- **config.py**
  - `_parse_machine` -> `_parse_unit`: read explicit `type` (default `vm`);
    default provider from type (vm->azure, pod->runpod) unless `provider:` given.
    Parse `ship` `src:dest`. Drop the `vm:` preset branch. Validate per type
    (`image` required for pod; warn on ignored fields).
- **providers/base.py**
  - Replace `kind` / `workloads` with: `create(unit, ...) -> Endpoint|None`,
    `start(endpoint, unit)` (systemd vs no-op), `expose(endpoint, unit)` (domain
    vs no-op), and capability flags used for config validation. Keep
    `resolve`/`list_sizes`/`destroy`/`pause`/`resume`.
- **providers/azure/provider.py + provision.py**
  - `create()` = current `provision()` returning an `Endpoint`. `start()` =
    systemd (move from pipeline). `expose()` = Caddy+DNS.
- **providers/runpod/provider.py**
  - Rewrite on **Pulumi** (`runpod/pulumi-runpod-native`: Pod + NetworkStorage)
    instead of the `runpod` SDK, so create/destroy match Azure's shape. `create()`
    builds a Pod (gpuTypeId/gpuCount/imageName/ports/env/volumeInGb, inject
    PUBLIC_KEY for SSH), returns an `Endpoint` (SSH host/port if available, else
    SSH-less). `start()`/`expose()` no-op. pause/resume may still need an API call
    if the Pulumi provider doesn't expose them — verify.
- **core/transfer.py**
  - `_rsync_dir` -> honor `(src, dest)` with the `~`/mkdir/trailing-slash handling
    above. Make `_wait_for_cloud_init` Azure-only (gate on the Endpoint; pods have
    no cloud-init). Thread `ssh_port` through `_connect`/`open_ssh`.
- **pipeline.py**
  - Collapse `_deploy_process`/`_deploy_container` into one `deploy()` that walks
    the six steps, gating ship/setup on `endpoint.has_ssh`. Keep the registry
    record + management routing (already done).
- **cli/main.py + templates/infra.yml + todo.md**
  - `--type`, drop `--vm` preset; document `unit`/`type`, `ship src:dest`, pod SSH.

## Out of scope / deferred (todo.md)

- Private GHCR pull (`pull_secret`) — public images only for now.
- `image` on a `vm` (auto-docker convenience).
- Multi-unit deploys (still single-unit).
- Live RunPod verification needs API key + GPU quota + a registry; structural only
  until then.
```
