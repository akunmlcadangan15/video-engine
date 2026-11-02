# Environment and variables

| Item | Value |
|---|---|
| Python | `/workspace/engine-venv` (`source /workspace/engine-venv/bin/activate`) |
| Rust | `source ~/.cargo/env`; build with `CC=/usr/bin/gcc` |
| Rust extension | `hypoverum_core_rs` (PyO3 0.27, abi3). Rebuild: `cd rust/hypoverum_core_rs && CC=/usr/bin/gcc maturin develop --release` |
| ffmpeg / ffprobe | 7.1 on PATH (override: `VIDEO_ENGINE_FFMPEG`, `VIDEO_ENGINE_FFPROBE`) |
| HyperFrames, Node, Chrome, Puppeteer | engine-owned, pinned in `vendor/VERSIONS.json` (Node v22.23.4 SHA-256 checked, hyperframes 0.8.214, puppeteer-core 25.16.0, three 0.186.1 via `vendor/npm/package-lock.json`, chrome-headless-shell 152.0.7977.30). Installed trees `vendor/node`, `vendor/npm/node_modules`, `vendor/chrome` are gitignored. Check: `engine doctor`; install: `engine doctor --install`. Override the whole directory with `VIDEO_ENGINE_VENDOR`. No other toolchain location is read. |
| Gemini | `GEMINI_API_KEY` in the environment. Never print it. |
| Other AI providers | API key in the env var named by `api_key_env` in `ai_providers.toml`. Never print it. |

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_ENGINE_THREADS` | 6 | CPU budget for encoders and Rust |
| `VIDEO_ENGINE_RUST_THREADS` | THREADS-2 | Rust rayon pool size (`core.set_threads`, set once at import) |
| `VIDEO_ENGINE_SHM` | `/dev/shm/video_engine` | scratch root; every build, preflight, preview, draft and director run uses its own dir `<project_id>-<pid>-<random>` under it (strictly inside, symlinks resolved) and deletes only that dir |
| `VIDEO_ENGINE_CACHE` | `<repo>/.cache` | BLAKE3 content-addressed cache (words, segments, map renders) |
| `VIDEO_ENGINE_CACHE_MAX_MB` | 4096 | cache size limit (LRU prune) |
| `VIDEO_ENGINE_HF_WORKERS` | `workers // 4` | HyperFrames capture workers (overrides the budget's `hf_workers`) |
| `VIDEO_ENGINE_WORKERS` | unset | build worker budget (overrides profile `[resources] workers`; see Resources) |
| `VIDEO_ENGINE_TASTE_DIR` | `<profiles>/taste` | director taste store directory |
| `VIDEO_ENGINE_VENDOR` | `<repo>/vendor` | toolchain directory |
| `VIDEO_ENGINE_AI_PROVIDER` | unset | AI provider when `--provider` is not given |
| `VIDEO_ENGINE_AI_CONFIG` | `<repo>/ai_providers.toml` | AI provider registry file |
| `VIDEO_ENGINE_PROFILES` | `<repo>/profiles` | channel profile directory |
