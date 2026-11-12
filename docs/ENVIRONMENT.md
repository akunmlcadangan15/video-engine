# Environment and variables

Where the engine runs and which variables configure it.

**What this document answers**

- Which platforms are supported (Linux x64 and macOS Apple Silicon), the parity policy and how to install on macOS.
- Which tools and versions the engine needs, and how to check them (`engine doctor`).
- How the build wrappers in `scripts/` work, including a final build on Modal.
- Which `VIDEO_ENGINE_*` variables exist, their defaults and what they change.

## Platforms

Linux x64 and macOS Apple Silicon (arm64) are equal platforms. The platform key is `linux-x64` or `darwin-arm64` (`video_engine/platform_info.py`); anything else (Intel Mac, Linux arm64, Windows, Python under Rosetta) is an error that `engine doctor` reports.

Parity policy:

- The same gates apply on both platforms, with the same profile; there is no platform-specific relaxation.
- CI verifies this parity: the `macos-fast` job runs the fast subset on `macos-latest` on every push and pull request, and the `macos-nightly` job runs the full suite with the vendored toolchain on schedule.
- Either platform builds and delivers: `final.mp4` and `engine deliver` are valid from Linux and from macOS.
- The output has the same spec (H.264 1080x1920, AAC 48 kHz, same loudness and QA limits) but is not bit-identical: ffmpeg, x264 and Chrome builds differ, so frames and bytes differ slightly and `final_sha256` differs between platforms.
- The cache is separated by toolchain identity (ffmpeg version, vendor pins, platform): an entry written on one platform is never served on the other, so a shared `VIDEO_ENGINE_CACHE` folder does not mix outputs.
- `engine doctor` prints a platform line and requires ffmpeg major version 7 or newer on both.

| | Linux x64 | macOS Apple Silicon |
|---|---|---|
| Node | `node-v<ver>-linux-x64` (SHA-256 per platform in `vendor/VERSIONS.json`) | `node-v<ver>-darwin-arm64` |
| Chrome | `linux-<ver>/chrome-headless-shell-linux64/` | `mac_arm-<ver>/chrome-headless-shell-mac-arm64/` |
| ffmpeg ≥ 7 | system package (`/usr/bin`) | Homebrew latest `ffmpeg` (or `ffmpeg@7`, keg-only) |
| Scratch root (`VIDEO_ENGINE_SHM`) | `/dev/shm/video_engine` (tmpfs, uses RAM) | `$TMPDIR/video_engine` (disk) |
| Process stats, RAM | `/proc` | `ps`, `sysctl` |

### Install on macOS (Apple Silicon)

```bash
xcode-select --install                                       # compiler toolchain and git
# install Homebrew first: https://brew.sh
brew install python@3.12 ffmpeg rustup                    # latest ffmpeg (>= 7); ffmpeg@7 is also supported
# Only needed if you installed the keg-only ffmpeg@7:
# export PATH="$(brew --prefix ffmpeg@7)/bin:$PATH"
rustup-init -y && source ~/.cargo/env
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
(cd rust/hypoverum_core_rs && maturin develop --release)     # Rust extension
engine doctor --install                                      # Node arm64, HyperFrames, Chrome mac-arm64 from vendor/VERSIONS.json
engine doctor                                                # platform line, ffmpeg >= 7 (latest), filters, encoders
```

- Homebrew links the latest `ffmpeg` into `PATH` normally, so the newest version is used by default. The older `ffmpeg@7` is keg-only and not linked; if you install it, either prepend `$(brew --prefix ffmpeg@7)/bin` as above or point the engine at it with `VIDEO_ENGINE_FFMPEG` and `VIDEO_ENGINE_FFPROBE` (full paths, e.g. `$(brew --prefix ffmpeg@7)/bin/ffmpeg`). The HyperFrames CLI uses the ffmpeg found through the same settings.
- Use an arm64 Python (`python3.12 -c "import platform; print(platform.machine())"` prints `arm64`).
- The default scratch root is `$TMPDIR/video_engine` (a per-user folder under `/var/folders/...`). It is on disk, not RAM; set `VIDEO_ENGINE_SHM` to another folder if the system volume is short of space.
- Build with the wrappers in `scripts/` (see [Build wrappers](#build-wrappers)); put `scripts/` on `PATH`, e.g. `export PATH="$PWD/scripts:$PATH"`.
- Rust tests: `TMPDIR` can stay at its macOS default (see [RUST.md](RUST.md)).

## Build wrappers

`scripts/engine-build` and `scripts/engine-batch` are POSIX `sh` wrappers around `engine build` and `engine batch` that behave the same on Linux and macOS (macOS has no `flock(1)`, so the lock uses Python `fcntl.flock`).
On the Hypoverum render server the same-named wrappers in `/workspace` do the same job; the lock file and the thresholds match.

Steps, in order:

1. Environment: sources `$VIDEO_ENGINE_ENV_FILE` (an error when set but missing), else `./engine.env` when it exists. Variables in it are exported.
2. Disk: `df -Pk` on the `out/` folder of every project named on the command line (the project folder when `out/` does not exist yet, the current folder when none is named). Refuses (exit 74) when less than `VIDEO_ENGINE_MIN_FREE_GB` is free.
3. Lock: exclusive, non-blocking `flock` on the render lock file, held by the engine process until it ends. A second render gets exit 75 and a message that names the lock file instead of waiting. On Linux the check `flock -n "$VIDEO_ENGINE_RENDER_LOCK" true` sees the same lock.
4. Run: `nice -n 10 engine build "$@"` (or `engine batch`). The exit code of the engine is the exit code of the wrapper (0 ok, 1 gate or engine error, 2 usage, 3 waiting for the agent).

Own exit codes: 75 lock busy, 74 not enough free disk, 127 `engine` not on `PATH`, 2 bad `VIDEO_ENGINE_MIN_FREE_GB` or missing env file.
The engine's own per-project lock (`out/.batch.lock`) is separate and still applies.

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_ENGINE_RENDER_LOCK` | `/workspace/.render.lock` if `/workspace` exists, else `${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/video-engine.render.lock` | global render lock file; one render at a time per lock |
| `VIDEO_ENGINE_MIN_FREE_GB` | 8 | minimum free disk (GB, decimals allowed) on the filesystem of `out/`; `0` disables the check |
| `VIDEO_ENGINE_ENV_FILE` | unset (then `./engine.env` if present, else `<user-config>/video-engine/engine.env`) | `KEY=value` file of machine-specific overrides. Wrappers source it in the shell; the engine also loads it (without overwriting variables already in the environment). |

Tests: `pytest -q tests/test_wrapper_scripts.py` (uses a stub `engine`).

A final build can run on Modal instead of this machine. Draft builds stay on `scripts/engine-build`. See [Remote final build](#remote-final-build).

## Tools and versions

| Item | Value |
|---|---|
| Python | `/workspace/engine-venv` on the render server (`source /workspace/engine-venv/bin/activate`); `.venv` in a checkout |
| Rust | `source ~/.cargo/env`; on the Linux server build with `CC=/usr/bin/gcc` (macOS: the default Apple clang) |
| Rust extension | `hypoverum_core_rs` (PyO3 0.27, abi3). Rebuild: `cd rust/hypoverum_core_rs && CC=/usr/bin/gcc maturin develop --release` (macOS: without `CC=`) |
| ffmpeg / ffprobe | major version 7 or newer on PATH (verified 7.1.5 and 9.0.2; macOS: Homebrew latest `ffmpeg` or `ffmpeg@7`, keg-only; override: `VIDEO_ENGINE_FFMPEG`, `VIDEO_ENGINE_FFPROBE`) |
| HyperFrames, Node, Chrome, Puppeteer, ffmpeg, Python, Modal | Default: use whatever is already on the machine, resolved by `video_engine.discover` in this order: env override (`VIDEO_ENGINE_NODE`, `VIDEO_ENGINE_CHROME`, `VIDEO_ENGINE_HYPERFRAMES`, `VIDEO_ENGINE_PUPPETEER`, `VIDEO_ENGINE_THREE`, `VIDEO_ENGINE_FFMPEG` / `FFPROBE`, `VIDEO_ENGINE_PYTHON`, `VIDEO_ENGINE_MODAL`) > global npm roots (`NODE_PATH`, `npm root -g`, platform-typical directories) > `PATH` > platform-standard cache/data dirs, the user toolchain directory (`<user-data>/video-engine/toolchain`), Puppeteer browser cache, and common install locations > `vendor/`. `engine doctor --install` installs the latest release of a *missing* tool into the user toolchain directory (not the lockfile). Pins in `vendor/VERSIONS.json` remain available through `VIDEO_ENGINE_HYPERFRAMES_VERSION=pinned` or `engine toolchain use pinned`. `VIDEO_ENGINE_VENDOR` still overrides the vendor directory. HyperFrames policy: `auto` (default), `pinned`, `latest`, `system`, or `X.Y.Z`. |
| Gemini (optional provider) | extra `gemini` (`google-genai`) and `GEMINI_API_KEY`. Never print the key. Unused unless that provider is selected. |
| Other AI providers | API key in the env var named by `api_key_env` in `ai_providers.toml`. Never print it. |

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_ENGINE_THREADS` | 6 | CPU budget for encoders and Rust |
| `VIDEO_ENGINE_RUST_THREADS` | THREADS-2 | Rust rayon pool size (`core.set_threads`, set once at import) |
| `VIDEO_ENGINE_SHM` | Linux: `/dev/shm/video_engine`; macOS: `$TMPDIR/video_engine` | scratch root; every build, preflight, preview, draft and director run uses its own dir `<project_id>-<pid>-<random>` under it (strictly inside, symlinks resolved) and deletes only that dir |
| `VIDEO_ENGINE_CACHE` | `<repo>/.cache` | BLAKE3 content-addressed cache (words, segments, map renders) |
| `VIDEO_ENGINE_CACHE_MAX_MB` | 4096 | cache size limit (LRU prune). Sound files pinned by a project lock are not deleted to meet it (docs/SOUND.md). |
| `VIDEO_ENGINE_HF_WORKERS` | `workers // 4` | HyperFrames capture workers (overrides the budget's `hf_workers`) |
| `VIDEO_ENGINE_WORKERS` | unset | build worker budget (overrides profile `[resources] workers`; see [Resources](BUILD.md#resources)) |
| `VIDEO_ENGINE_TASTE_DIR` | `<profiles>/taste` | director taste store directory |
| `VIDEO_ENGINE_VENDOR` | `<repo>/vendor` | toolchain directory |
| `VIDEO_ENGINE_HYPERFRAMES_VERSION` | unset (`vendor/hyperframes.select`, else `auto`) | HyperFrames the build renders with: `auto` (machine-first, the default), `pinned` (vendor lockfile, opt-in), `latest`, `system` or `X.Y.Z` ([`engine toolchain`](CLI.md#engine-toolchain-show--list--use-spec)) |
| `VIDEO_ENGINE_AI_PROVIDER` | unset (then `default` in the AI config, else `agent`) | AI provider when `--provider` is not given |
| `VIDEO_ENGINE_AI_MODEL` | unset | agent provider: the name of the model that answers, written to the reports (wins over `[agent] model_label`) |
| `VIDEO_ENGINE_AI_CONFIG` | `<repo>/ai_providers.toml` | AI provider registry file |
| `VIDEO_ENGINE_PROFILES` | `<repo>/profiles` | channel profile directory |
| `VIDEO_ENGINE_WORKSPACE` | `<user-data>/video-engine` (Linux: `$XDG_DATA_HOME` or `~/.local/share`; macOS: `~/Library/Application Support`; Windows: `%LOCALAPPDATA%`) | folder that holds the shared libraries; the defaults of the four variables below sit in it. Machine-specific paths go in a local `engine.env`, not in the default. |
| `VIDEO_ENGINE_GEO_LIBRARY` | `<workspace>/geo-library` | Natural Earth `land` GeoJSON for gate G02 (absent: the land check skips). The finest file present is used: `ne_10m_land.geojson`, then `ne_50m`, then `ne_110m` |
| `VIDEO_ENGINE_LOCAL_TONES` | unset | `1` allows generated local-test SFX tones when the sound cache has no licensed file. Unset: that kind is an error (the build requests the file instead) |
| `VIDEO_ENGINE_OPEN_ASSETS` | `<workspace>/open-assets` | `open-assets/catalog.csv` and the files it lists |
| `VIDEO_ENGINE_STOCK_DIRS` | `<workspace>/asset-cache` | stock and B-roll roots, separated by the platform path separator (`:` on Unix, `;` on Windows) |
| `VIDEO_ENGINE_DOWNLOAD_DIR` | `<workspace>/downloads` | folder searched for assets downloaded earlier (asset resolver) |
| `VIDEO_ENGINE_ASSET_ROOTS` | unset | extra asset search roots (same path separator), looked up before the per-library defaults |

## Remote final build

`scripts/engine-build-modal` runs a final `engine build` in an ephemeral Modal container and copies `out/` back. It does not deploy an app. Draft builds (`--mode draft`, proxy quality) are not accepted; use `scripts/engine-build` for those.

The command loads `engine.env` the same way as `scripts/engine-build` (`VIDEO_ENGINE_ENV_FILE`, else `./engine.env`, else the user-config file; an explicit path that is missing is exit 2; already-set variables win). It then holds the same render lock for the upload, the remote build and the download.

```bash
scripts/engine-build-modal PROJECT --dry-run
scripts/engine-build-modal PROJECT --cpu 8 --memory 16384 --timeout 10800
scripts/engine-build-modal PROJECT --gpu L4 --keep-volume
scripts/engine-build-modal --selftest
scripts/engine-build-modal PROJECT --fetch RUN_ID
```

Order for a real run:

1. Free-disk check on `PROJECT/out` (exit 74 under `VIDEO_ENGINE_MIN_FREE_GB`, same rule as `scripts/engine-build`; `0` disables it).
2. Render lock (exit 75 when another render holds it).
3. `engine validate` and `engine preflight` locally, in release mode. Their exit code is returned as-is. Preflight does not pass `--write-fixes` or `--captions`.
4. Closure of the files the build will open. A missing file is printed and the command exits 1 before any upload.
5. Upload of that closure onto a Modal Volume, then one container, then a chunked download into `PROJECT/out`.

`--dry-run` runs steps 3 and 4, prints the plan, and does not take the lock or call Modal. `--selftest` needs no project: it prints `engine doctor` inside the image and exits 1 when a major component disagrees with this machine. `--fetch RUN_ID` skips validation and upload and only downloads a finished run (the lock and the disk check still apply).

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--cpu N` | 8 | CPU request and CPU limit, the tuple `(N, N)`, in physical cores (1–64) |
| `--memory MB` | 16384 | memory request and memory limit, the tuple `(MB, MB)` |
| `--timeout S` | 10800 | hard container timeout in seconds (1–86400) |
| `--disk MB` | unset | ephemeral disk in MiB. Omit it and the container uses Modal's default disk. A set value must be 524288–3145728. `--disk` wins over `VIDEO_ENGINE_MODAL_DISK_MB` |
| `--gpu none\|L4` | `none` | no GPU unless this is `L4` |
| `--keep-volume` | off | leave `/runs/<id>` on the volume after a successful fetch |
| `--dry-run` | off | local validate, preflight and closure only |
| `--selftest` | off | compare in-image `engine doctor` with this machine |
| `--fetch RUN_ID` | unset | resume the download of that run |

The container is created with `min_containers=0`, `max_containers=1`, `scaledown_window=2`, `single_use_containers=True` and `retries=0`. Nothing is deployed.

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_ENGINE_MODAL_VOLUME` | `video-engine-builds` | Volume name. Created as version 2 when missing |
| `VIDEO_ENGINE_MODAL_CHUNK_MB` | 64 | output chunk size (1–1024) |
| `VIDEO_ENGINE_MODAL_DISK_MB` | unset | ephemeral disk in MiB when `--disk` is omitted. Allowed only 524288–3145728. Unset leaves Modal's default; the wrapper does not send a disk size |
| `VIDEO_ENGINE_MIN_FREE_GB` | 8 | reserve on the local filesystem. Chunk writes must leave strictly more than this free. `0` disables the reserve; the chunk itself must still fit |
| `VIDEO_ENGINE_RENDER_LOCK` | same path as `scripts/engine-build` | held for the remote build and for `--fetch` |

Exit codes: 0 published (or a self-test match, or a resumed fetch), 1 missing inputs, remote failure, transfer error or self-test mismatch, 2 usage or a bad environment file, 74 disk, 75 lock busy, 127 `engine` or the `modal` package is not on `PATH`. A non-zero status from local `engine validate` or `engine preflight` is returned unchanged. The remote `engine build` status is returned after a successful download, including when the build wrote `out/build_report.failed.json` and did not publish `final.mp4`.

### What is uploaded

The closure is computed locally from `master_timeline.json`, the sound lock in `out/build_manifest.json` and `out/locked-sounds/`, caption inputs, and the profile files the timeline's profile reads. Symlinks are followed. The bytes that are uploaded are the target of the link; inside the container that path is a regular file at the same project-relative location. The timeline JSON is not edited.

Configured roots are respected: `VIDEO_ENGINE_WORKSPACE`, `VIDEO_ENGINE_GEO_LIBRARY`, `VIDEO_ENGINE_OPEN_ASSETS`, `VIDEO_ENGINE_STOCK_DIRS`, `VIDEO_ENGINE_DOWNLOAD_DIR`, `VIDEO_ENGINE_ASSET_ROOTS`, `VIDEO_ENGINE_CACHE`, and `VIDEO_ENGINE_PROFILES` when that directory is outside the checkout. A file under one of those roots is stored at the same relative path. In the container the matching `VIDEO_ENGINE_*` variable points at `/work/roots/...` (the cache root is `/work/cache`). Scheme references (`open:`, `project:`, `vendor:`, `download:`, `cache:`, `engine:`) resolve through those roots. An absolute path named by the timeline is also recreated as a symlink to the uploaded file, except under system directories (`/etc`, `/usr`, `/opt`, and the other root-level system trees).

Also included when they exist: the Natural Earth land and admin-0 files the geo gate and the map loader open (not the rest of the geo library); `catalog.csv` or `index.csv` for a library that contributed a file; JSON sidecars beside a shipped media file; the voiceover; optional caption sidecars next to it (`*.captions.json`, `*.words.json`, `.ass`, `.srt`, `.vtt`). Pinned caption words travel inside `master_timeline.json`. Files that already live in the image (the checkout's `vendor/` and `profiles/` when the profile directory is the repo copy) are not uploaded.

Blobs are stored at `/cas/blobs/<sha256[:2]>/<sha256>`. A blob whose sha256 is already on the volume is not uploaded again. Each run also writes `/runs/<run_id>/layout.json`. The run id is printed before the upload.

Not uploaded: `out/final.mp4`, previous reports, the draft tree, or any cache file the timeline and the sound lock do not name. The remote segment cache therefore starts empty. Locked sounds are present, so the build does not ask the sound resolver for them again.

### Cost control

The build is CPU-bound (headless Chrome and x264). The default container has no GPU. `--cpu` and `--memory` set both the request and the limit, so the bill does not depend on a burst above the request: Modal charges the greater of the request and the observed use, up to the limit, for the wall time of the container. The request-based products are recorded on the report (`cpu_request_core_seconds`, `memory_request_gib_seconds`). `--timeout` is a hard stop. `min_containers=0` and a two-second scaledown window mean the container does not stay up after the run. `single_use_containers` and `retries=0` mean a failed attempt is not run again automatically. `--gpu L4` is the only way to attach a GPU, and the pipeline does not use one.

`--dry-run` prints `upload_bytes` before any transfer. After a successful fetch the run directory is deleted unless `--keep-volume` is set. Content-addressed blobs stay, so the next run skips unchanged files. Volume storage is billed until those blobs are removed (`modal volume rm`). Draft and proxy renders stay on the local machine.

### Resume a download

The remote side writes `out/` as a manifest of chunks, each `{name, size, sha256}`, plus the whole-file size and sha256. The local side keeps a verified prefix under `PROJECT/out/.modal-fetch/<run_id>/`. A missing or mismatched chunk ends the prefix; later chunks are discarded and fetched again. Each chunk is checked, then the assembled file is checked, then `os.replace` puts it in place. A failed assemble leaves the previous file untouched.

If the download stops, the run stays on the volume and the command prints `--fetch RUN_ID`. That flag repeats the download and then deletes the run unless `--keep-volume` is set. `PROJECT/out/modal_fetch.json` is a local receipt written after the verified files are in place. It is not part of the build hash.

### Troubleshooting

- `modal` is missing: `pip install modal`, then `modal setup`. Exit 127.
- Exit 75: another render holds the lock. The message names the file. `flock -n "$VIDEO_ENGINE_RENDER_LOCK" true` sees the same lock.
- Exit 74: not enough free local disk for the reserve or for the next chunk. Lower `VIDEO_ENGINE_MIN_FREE_GB` only when the filesystem can hold `final.mp4`.
- Dry-run lists `missing:`: a timeline path, an `open:` / `download:` reference, a profile include, or a locked sound is absent from both the cache and `out/locked-sounds/`. A sha256 that does not match the lock is named the same way. Fix the file or run a local `engine build --relock` before the remote build.
- Self-test mismatch: the image's FFmpeg is the static GPL build pinned in `vendor/ffmpeg-static.json` (8.1, with libx264). Node, HyperFrames, puppeteer-core, three and chrome-headless-shell are installed into `vendor/` from `vendor/VERSIONS.json` unless `engine doctor` on this machine resolved a parseable version, in which case that version is a Docker build arg. The image has no second toolchain, so `engine doctor` in the container reports those vendor copies. A HyperFrames line that says pinned, or that names the vendor tree, is that layout; it is not a change of the `auto` policy. The self-test compares version numbers: FFmpeg, Node and Chrome by major, HyperFrames, puppeteer-core and three by full `x.y.z`. The image's FFmpeg major must be at least 8.
- Captions that are not pinned, and sounds that are not already on disk, are not fetched inside the container. Pin words locally (`engine words`) and resolve sounds locally first. The container does not receive API keys from this command.
- A Volume created by an older client at the same name can refuse version 2. Remove it or set `VIDEO_ENGINE_MODAL_VOLUME` to a new name.
- Ephemeral disk: leave `--disk` and `VIDEO_ENGINE_MODAL_DISK_MB` unset to use Modal's default. A value outside 524288–3145728 MiB is a usage error (exit 2) and is rejected before any upload or container start.
- `modal_render.py` does not build and does not request a GPU. Use this command.

Image definition: `Dockerfile.modal`, `scripts/modal_image_setup.py`, `modal_build.py`. The image installs the static FFmpeg build, the Node tarball, the npm packages, chrome-headless-shell, and the Rust extension (`maturin`), then runs `engine doctor`. Python sources mounted for the run are the client tree; the image supplies the tools and the extension. A Rust change needs a new image build.

Tests: `pytest -q tests/test_modal_build.py` (closure, chunked download, argument parsing, dry-run text; no Modal calls).
