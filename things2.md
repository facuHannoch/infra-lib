Here's the staged flow we converged on — the conceptual pipeline, and which of the two axes owns each stage:

## The stages

| #   | Stage                 | What it does                                                                            | Owned by               |
| --- | --------------------- | --------------------------------------------------------------------------------------- | ---------------------- |
| 1   | **Provision**         | Create the substrate (the VM / the box)                                                 | **Provider** (vm axis) |
| 2   | **Prepare**           | Configure the substrate — install runtime deps (e.g. Docker, drivers)                   | **Provider** (vm axis) |
| 3   | **Deliver / Package** | Produce + place the artifact: `process` → rsync files; `container` → build & push image | **Workload** axis      |
| 4   | **Configure-app**     | Make the artifact run: `process` → systemd service; `container` → CMD/command override  | **Workload** axis      |
| 5   | **Expose**            | Reverse proxy / domain / public URL                                                     | **Provider** (vm axis) |

## The two axes

- **Provider axis (where it runs)** — `kind: vm` vs `container_host`. Governs **1, 2, 5** (provision / prepare / expose).
- **Workload axis (what runs)** — `process` (raw files on the box) vs `container` (an image). Inferred from config (`image:`/`build:` present → container). Governs **3, 4** (deliver / configure-app).

## The key insight — your "skip 1 and 2"

A **VM provider** (Azure) walks all five stages.

A **container host** (RunPod) **collapses 1, 2, 5 into a single `launch()` call** — you don't provision-then-prepare-then-expose; the platform's "create pod" does substrate + run + proxy URL in one shot. That's why RunPod's provider does `provision() → NotImplementedError` and lives entirely through `launch()`.

## The "2-vs-3.iii duplication" we resolved

You flagged that "configure the box (install docker)" and "configure the app inside the container" looked like the same step twice. We decided they're **not** duplication — they have **different owners**:
- Stage 2 is the **substrate** owner (provider: "this box needs Docker").
- Stage 4 is the **app** owner (workload: "this image runs `python serve.py`").

Same verb ("configure"), different layer — so they stay separate.

The compatibility gate at the top of `deploy()` is what enforces that the two axes are legal together (Azure can't run `container`, RunPod can't run `process`) before any stage executes.

Want me to drop this as a comment/diagram into `pipeline.py` or `todo.md` so it's recorded, since it's the load-bearing mental model?


