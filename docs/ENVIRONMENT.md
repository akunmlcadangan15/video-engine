# Environment and variables

Where the engine runs and which variables configure it.

**What this document answers**

- Which platforms are supported (Linux x64 and macOS Apple Silicon), the parity policy and how to install on macOS.
- Which tools and versions the engine needs, and how to check them (`engine doctor`).
- How the build wrappers in `scripts/` work.
- Which `VIDEO_ENGINE_*` variables exist, their defaults and what they change.

## Platforms

Linux x64 and macOS Apple Silicon (arm64) are equal platforms. The platform key is `linux-x64` or `darwin-arm64` (`video_engine/platform_info.py`); anything else (Intel Mac, Linux arm64, Windows, Python under Rosetta) is an error that `engine doctor` reports.

Parity policy:

- The same gates apply on both platforms, with the same profile; there is no platform-specific relaxation.
- CI memeriksa kesetaraan ini: job `macos-fast` menjalankan subset fast di `macos-latest` setiap push/pull request, dan job `macos-nightly` menjalankan suite penuh dengan toolchain vendor setiap jadwal.
- Either platform builds and delivers: `final.mp4` and `engine deliver` are valid from Linux and from macOS.
- The output has the same spec (H.264 1080x1920, AAC 48 kHz, same loudness and QA limits) but is not bit-identical: ffmpeg, x264 and Chrome builds differ, so frames and bytes differ slightly and `final_sha256` differs between platforms.
- The cache is separated by toolchain identity (ffmpeg version, vendor pins, platform): an entry written on one platform is never served on the other, so a shared `VIDEO_ENGINE_CACHE` folder does not mix outputs.
- `engine doctor` prints a platform line and requires ffmpeg major version 7 on both.

| | Linux x64 | macOS Apple Silicon |
|---|---|---|
| Node | `node-v<ver>-linux-x64` (SHA-256 per platform in `vendor/VERSIONS.json`) | `node-v<ver>-darwin-arm64` |
| Chrome | `linux-<ver>/chrome-headless-shell-linux64/` | `mac_arm-<ver>/chrome-headless-shell-mac-arm64/` |
| ffmpeg 7-9 | system package (`/usr/bin`) | Homebrew `ffmpeg@7` (keg-only, see below) or `ffmpeg` 9 |
| Scratch root (`VIDEO_ENGINE_SHM`) | `/dev/shm/video_engine` (tmpfs, uses RAM) | `$TMPDIR/video_engine` (disk) |
| Process stats, RAM | `/proc` | `ps`, `sysctl` |

### Install on macOS (Apple Silicon)

```bash
xcode-select --install                                       # compiler toolchain and git
# install Homebrew first: https://brew.sh
brew install python@3.12 ffmpeg@7 rustup
export PATH="$(brew --prefix ffmpeg@7)/bin:$PATH"            # ffmpeg@7 is keg-only; put this in ~/.zshrc
rustup-init -y && source ~/.cargo/env
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
(cd rust/hypoverum_core_rs && maturin develop --release)     # Rust extension
engine doctor --install                                      # Node arm64, HyperFrames, Chrome mac-arm64 from vendor/VERSIONS.json
engine doctor                                                # platform line, ffmpeg major 7, filters, encoders
```

- `ffmpeg@7` is keg-only: Homebrew does not link it into `PATH`. Either prepend `$(brew --prefix ffmpeg@7)/bin` as above, or point the engine at it with `VIDEO_ENGINE_FFMPEG` and `VIDEO_ENGINE_FFPROBE` (full paths, e.g. `$(brew --prefix ffmpeg@7)/bin/ffmpeg`). The HyperFrames CLI uses the ffmpeg found through the same settings.
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
| `VIDEO_ENGINE_ENV_FILE` | unset (then `./engine.env` if present) | shell-syntax file of `KEY=value` lines sourced before the checks (for example `VIDEO_ENGINE_CACHE`, `VIDEO_ENGINE_THREADS`) |

Tests: `pytest -q tests/test_wrapper_scripts.py` (uses a stub `engine`).

## Tools and versions

| Item | Value |
|---|---|
| Python | `/workspace/engine-venv` on the render server (`source /workspace/engine-venv/bin/activate`); `.venv` in a checkout |
| Rust | `source ~/.cargo/env`; on the Linux server build with `CC=/usr/bin/gcc` (macOS: the default Apple clang) |
| Rust extension | `hypoverum_core_rs` (PyO3 0.27, abi3). Rebuild: `cd rust/hypoverum_core_rs && CC=/usr/bin/gcc maturin develop --release` (macOS: without `CC=`) |
| ffmpeg / ffprobe | major version 7 to 9 on PATH (verified 7.1.5 and 9.0.2; macOS: Homebrew `ffmpeg@7`, keg-only; override: `VIDEO_ENGINE_FFMPEG`, `VIDEO_ENGINE_FFPROBE`) |
| HyperFrames, Node, Chrome, Puppeteer | engine-owned, pinned in `vendor/VERSIONS.json` (Node v22.23.4 SHA-256 checked per platform, hyperframes 0.9.13, puppeteer-core 25.16.0, three 0.186.1 via `vendor/npm/package-lock.json`, chrome-headless-shell 152.0.7977.30). Installed trees `vendor/node`, `vendor/npm/node_modules`, `vendor/chrome` are gitignored. Check: `engine doctor`; install: `engine doctor --install`. Override the whole directory with `VIDEO_ENGINE_VENDOR`. The HyperFrames version is selectable (`VIDEO_ENGINE_HYPERFRAMES_VERSION` or `engine toolchain use`: `pinned`, `X.Y.Z` in `vendor/hf/`, `latest`, `system` = global CLI); no other toolchain location is read. |
| Gemini | `GEMINI_API_KEY` in the environment. Never print it. |
| Other AI providers | API key in the env var named by `api_key_env` in `ai_providers.toml`. Never print it. |

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_ENGINE_THREADS` | 6 | CPU budget for encoders and Rust |
| `VIDEO_ENGINE_RUST_THREADS` | THREADS-2 | Rust rayon pool size (`core.set_threads`, set once at import) |
| `VIDEO_ENGINE_SHM` | Linux: `/dev/shm/video_engine`; macOS: `$TMPDIR/video_engine` | scratch root; every build, preflight, preview, draft and director run uses its own dir `<project_id>-<pid>-<random>` under it (strictly inside, symlinks resolved) and deletes only that dir |
| `VIDEO_ENGINE_CACHE` | `<repo>/.cache` | BLAKE3 content-addressed cache (words, segments, map renders) |
| `VIDEO_ENGINE_CACHE_MAX_MB` | 4096 | cache size limit (LRU prune) |
| `VIDEO_ENGINE_HF_WORKERS` | `workers // 4` | HyperFrames capture workers (overrides the budget's `hf_workers`) |
| `VIDEO_ENGINE_WORKERS` | unset | build worker budget (overrides profile `[resources] workers`; see [Resources](BUILD.md#resources)) |
| `VIDEO_ENGINE_TASTE_DIR` | `<profiles>/taste` | director taste store directory |
| `VIDEO_ENGINE_VENDOR` | `<repo>/vendor` | toolchain directory |
| `VIDEO_ENGINE_HYPERFRAMES_VERSION` | unset (`vendor/hyperframes.select`, else `pinned`) | HyperFrames the build renders with: `pinned`, `latest`, `system` or `X.Y.Z` ([`engine toolchain`](CLI.md#engine-toolchain-show--list--use-spec)) |
| `VIDEO_ENGINE_AI_PROVIDER` | unset | AI provider when `--provider` is not given |
| `VIDEO_ENGINE_AI_CONFIG` | `<repo>/ai_providers.toml` | AI provider registry file |
| `VIDEO_ENGINE_PROFILES` | `<repo>/profiles` | channel profile directory |
| `VIDEO_ENGINE_WORKSPACE` | `/workspace` (Linux), `~/workspace` (macOS) | folder that holds the shared libraries; the defaults of the four variables below sit in it |
| `VIDEO_ENGINE_GEO_LIBRARY` | `<workspace>/geo-library` | Natural Earth `land` GeoJSON for gate G02 (absent: the land check skips) |
| `VIDEO_ENGINE_OPEN_ASSETS` | `<workspace>/open-assets` | `open-assets/catalog.csv` and the files it lists |
| `VIDEO_ENGINE_STOCK_DIRS` | `<workspace>/asset-cache` | stock and B-roll roots, separated by `:` |
| `VIDEO_ENGINE_DOWNLOAD_DIR` | `<workspace>/downloads` | folder searched for assets downloaded earlier (asset resolver) |
