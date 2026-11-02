# Proposal: a render lock that allows safe parallel builds

Status: design only. The wrapper that holds the lock is not in this repository, so nothing here was tested.

**What this document answers**

- What the global render lock does today, as far as the repository shows.
- Why a per-project lock alone is not enough, and what could replace the global lock.
- What must be known before changing it.

## What the repository shows

- The wrapper `engine-build` (and `engine-batch`) holds `/workspace/.render.lock`, refuses to run with less than 8 GB of free disk, loads `engine.env` and runs the build with `nice` (AGENTS.md).
  Its source is **not in this repository**, so its exact behaviour is unconfirmed.
- The engine has two locks of its own:
  - `engine build` and `engine batch` take a per-project exclusive lock on `<project>/out/.batch.lock` (`video_engine/batch.py`, `ProjectLock`): the same project is never built twice at once.
  - Cache files are protected by leases, so one build cannot evict what another build uses (docs/BUILD.md, Cache leases).
- One build limits itself with a worker budget (`workers`, `native_workers`, `hf_workers`, `mux_threads`; docs/BUILD.md, Resources).
  Nothing limits the *sum* over several builds that start at the same time.

## Why a per-project lock is not enough

Two different projects can be built together safely as far as files go, but both would take the full worker budget, and Chrome plus x264 can exhaust CPU and RAM on a small machine.
The global lock prevents that by running one build at a time.

## Options

1. **Keep the global lock** (no change). Safe; one build at a time, even when the machine could do two small ones.
2. **Slots.** The engine keeps N lock files (for example `<workspace>/.render-slot-0` to `N-1`); a build takes the first free one with `flock` and sizes its worker budget to `workers / N`.
   The wrapper becomes a thin caller. N is a machine setting (`VIDEO_ENGINE_RENDER_SLOTS`, default 1, which equals today's behaviour).
3. **Memory-aware slots.** As option 2, but a build waits until free RAM and disk exceed its estimate. Needs a measured RAM peak per build; the build report already records `peak_rss_mb` per stage.

## Recommendation

Do option 2 after two things are known: the content of the wrapper, and the RAM peak of a HyperFrames-heavy build next to a native-heavy build on the render machine (`build_report.json` -> `resources`).
Until then keep the global lock.

## Questions for the owner

- May the wrapper be added to the repository (for example under `scripts/`) so it can be changed and tested?
- How many builds does the render machine run well together?
