# Agent lifecycle test

A test you (an LLM agent) run to answer two questions at once:

1. **Is `infra-lib` actually usable by an LLM?** You must drive a full deployment
   lifecycle using only the documented CLI / Python API. Wherever you hesitate, have
   to guess, or hit friction — that's a finding. Log it.
2. **Does state survive pause/resume?** You create a file on the machine, pause it,
   resume it, and check the file is still there — and that the server comes back.

Run it **once per provider** (a `vm` on Azure and a `pod` on RunPod). It is a *real*
deploy: it costs money and must be torn down at the end (step 9 is mandatory).

> This is deliberately goal-oriented, not copy-paste. Each step says *what to
> achieve* and *how to verify*; you choose the exact command from the docs/`--help`.
> The objective checks live in `lifecycle_check.py` (prints `PASS:`/`FAIL:`, exits 0/1).

---

## Prerequisites

- `pip install -e .` from the repo root; `infra-lib --help` works.
- **vm run:** authenticated with Azure (`infra-lib auth azure` or `ARM_*` env vars).
- **pod run:** `pip install pulumi-runpod runpod`; `infra-lib auth runpod --api-key …`;
  and choose an **SSH-capable image that serves an HTTP port** (steps 2/4/8 need SSH,
  step 3 needs the port). A `runpod/*` base image with `sshd` works.
- Run the checks from the repo root so `import infra_lib` resolves, e.g.
  `python test/lifecycle_check.py url <name>`.

## Per-provider parameters

Fill these in for the run; everything below refers to them.

| | **vm (Azure)** | **pod (RunPod)** |
|---|---|---|
| NAME | `life-vm` | `life-pod` |
| deploy shape | `--type vm --size small --port 8000 --start "python3 -m http.server 8000"` | `--type pod --gpu a40 --port 8000 --image <ssh+http image> --start "<serve on :8000>"` |
| MARKER_PATH (**persistent**) | `/srv/files/marker.txt` | `/workspace/marker.txt` ← **verify this is the volume mount** |
| URL source | `http://<ip>` (no domain) | the `…proxy.runpod.net` URL |
| curl TLS | plain http | add `--insecure` if the proxy cert trips verification |

> **The single most important thing:** on a **pod**, only the **mounted volume**
> survives stop/resume — the container filesystem is wiped. `MARKER_PATH` *must* be on
> the volume, or step 8 will fail misleadingly. The volume mount path is itself
> something this run should confirm (RunPod commonly mounts at `/workspace`).

---

## The lifecycle (9 steps)

### 1. Create a machine running a server
**Goal:** deploy NAME with a long-running HTTP server on a port.
**Do:** `infra-lib deploy` with the per-provider deploy shape above.
**Verify:** `python test/lifecycle_check.py url NAME` → prints an IP/URL and `ssh=yes`.
(If `ssh=no` on a pod, the image isn't exposing SSH — steps 2/4/8 can't run; fix the image.)

### 2. Connect and create a file
**Goal:** write a known marker to **MARKER_PATH** (on the persistent disk/volume).
**Do (agent-friendly):** the non-interactive verb — `infra-lib connect NAME -e "<cmd>"`
or the API `infra_lib.run(NAME, "<cmd>")`. *(Note for your usability log: the
interactive `infra-lib connect` can't be driven by an agent — only `-e`/`run` can.)*
**Verify:** `python test/lifecycle_check.py write NAME --path MARKER_PATH --marker hello-<run-id>` → `PASS`.

### 3. curl the server
**Goal:** confirm the server actually responds on its public URL.
**Verify:** `python test/lifecycle_check.py http NAME` (add `--insecure` if needed) → `PASS: GET … -> 200`.

### 4. Disconnect
**Goal:** ensure no SSH session is held open. Using `connect -e` / `run` already
closes the connection each call, so there's nothing to leave dangling — confirm you
are not sitting in an interactive shell.

### 5. List — the deployment is there
**Verify:** `python test/lifecycle_check.py listed NAME` → `PASS`. Also eyeball
`infra-lib list` and confirm NAME shows with the right provider/URL.

### 6. Pause
**Goal:** stop compute, keep the disk/volume.
**Do:** `infra-lib pause NAME`. **Verify:** it reports paused (vm: deallocated;
pod: GPU released). Optionally confirm `http NAME` now fails/declines.

### 7. Resume
**Do:** `infra-lib resume NAME`. **Verify:** it reports resumed. Give it a moment to boot.

### 8. Reconnect — the file is still there
**Goal:** prove persistence and that the server came back.
**Verify:**
- `python test/lifecycle_check.py read NAME --path MARKER_PATH --expect hello-<run-id>` → `PASS` (the file survived).
- `python test/lifecycle_check.py http NAME` → `PASS` again (the server auto-restarted: systemd on a vm, container CMD on a pod).

### 9. Destroy (mandatory)
**Do:** `infra-lib down NAME`. **Verify:** `python test/lifecycle_check.py listed NAME`
now → `FAIL` (gone), and the provider console shows nothing left running. **Do not
skip this — it's what stops the billing.**

---

## Pass criteria

The run **passes** if every check is `PASS` in order and step 9 leaves nothing behind.
The two load-bearing assertions:

- **Step 8 read** — the marker survived pause→resume (state persistence).
- **Step 8 http** — the server came back by itself after resume.

## Usability log (the real point)

While running, record anything that made this harder than it should be for an agent:

- Did you have to scrape stdout for the URL, or guess the deploy flags? *(Known gap:
  no `--json` output — the helper uses `infra_lib.get().url` to work around it.)*
- Did pod SSH "just work", or did you have to hunt for an SSH-capable image / the
  volume mount path?
- Were the error messages actionable when something was off (wrong type/provider,
  missing creds, no port)?
- Anything you expected the library to do that it didn't.

File the findings as items in `todo.md` (or report them back). Frictions here are the
deliverable as much as the PASS/FAIL line.

## Cleanup checklist (if you bail early)

- `infra-lib down NAME` for any deployment you created (vm **and** pod).
- `infra-lib list` should be empty of `life-*` names.
- Check the Azure portal / RunPod console once to be sure nothing is still billing.
