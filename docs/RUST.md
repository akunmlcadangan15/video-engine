# hypoverum_core_rs — guide for agents

Rules for the native crate `hypoverum_core_rs`.

**What this document answers**

- Which modules the crate has and what each does.
- The exact Python API of the crate: arguments, errors and limits.
- How to build, test and install the crate.
- The PyO3 rules (no panics across the boundary, GIL release, validation) and the frame, time and `.ass` rules.
- Which inputs a cache key must include, and the checklist for committing a Rust change.

Native core of `video_engine`. PyO3 0.27 (abi3, Python >= 3.10) + maturin + rayon + blake3. Built as `cdylib` (Python module) and `rlib` (Rust tests).

## Layout

| Path | Purpose |
|---|---|
| `rust/hypoverum_core_rs/Cargo.toml` | Crate manifest. Feature `py` (default) enables `pyo3`. |
| `rust/hypoverum_core_rs/pyproject.toml` | maturin config. Adds `pyo3/extension-module` at build time. |
| `rust/hypoverum_core_rs/hypoverum_core_rs.pyi` | Type stubs. maturin installs them as `hypoverum_core_rs/__init__.pyi` plus `py.typed`. Update with every signature change. |
| `src/lib.rs` | `ALGO_VERSION`, `THREADS_RANGE`, `hash_file` (chunked BLAKE3), and `mod py` (`#[cfg(feature = "py")]`): every `#[pyfunction]` and the `#[pymodule]`. No domain logic in `mod py`: convert types, call into a module, map errors. |
| `src/num.rs` | Limits (`MAX_FRAME = 2^40`, `MAX_SIDE = 32768`, `MAX_BUF = 2^31` bytes) and checked numeric helpers: `clampf` (NaN-safe), `f64_to_i64`, `f64_to_index`, `weight256`, `usize_to_f64`, `finite`, `positive`, `rgb_len`. Use these instead of `as` casts and `f64::clamp`. |
| `src/frames.rs` | Frame math: `check_fps` (1..=99), seconds->frame, frame->.ass centiseconds (integer, checked), centiseconds->`h:mm:ss.cc`. |
| `src/ass.rs` | .ass caption compiler: `Style::validate`, `check_words`, grouping words into captions, escaping (`esc`), events. |
| `src/camera.rs` | Camera presets, `check_focus_box`, `Win::check`, per-frame view windows (`MAX_WINDOWS = 2^24`), motion estimate, bilinear RGB24 warp (rayon over rows; allocates its output). |
| `src/silence.rs` | Pause detection on mono `i16` PCM: `frame_levels` (RMS dBFS per video frame, integer sample ranges `[f*rate/fps, (f+1)*rate/fps)`), `adaptive_threshold` (p90 - 24 dB, clamped to `-119..=-30`), `pauses_from_levels` (runs below threshold >= `min_frames`), `suggest_boundaries` (cuts at pause midpoints). Rates 1000..=192000 Hz. |
| `src/proxy.rs` | ffmpeg orchestration (argument vectors, no shell): `proxy_args`/`make_proxy` (H.264 proxy, height even 64..=1080, fps 1..=30, writes `<out>.part` then renames), `decode_pcm` (mono s16le through a pipe, max 6 h at 16 kHz). |
| `src/pixel.rs` | Pixel QA on RGB24 frames (`check_frames`: >= 4x4, exact length): luma mean, black fraction, per-third std/detail, motion (rayon over frames). |

Python callers: `video_engine/timeline.py` (sec_to_frame), `pauses.py` (detect_pauses_file, suggest_boundaries), `ai/tasks.py` (make_proxy), `captions.py` (compile_ass), `native.py` (camera_*, warp_frame), `qa.py`, `gates/layer2.py`, `gates/layer3.py` (camera_plan, frame_stats, frame_motion), `util.py` (blake3_*).

## Python API

Frames are `int` (i64), `0 <= frame < 2^40`.
Pixel buffers are `bytes`, RGB24, row-major, `len == w*h*3`, each side `1..=32768` and `w*h*3 <= 2^31`.
Windows (`W4`) are `(x, y, w, h)` floats in source pixels: finite, `w > 0`, `h > 0`.
No function raises `PanicException`; the errors listed are the only ones.

```
ALGO_VERSION: str                                  # "3"; part of every render cache key (see [Cache-key rules](#cache-key-rules))
version() -> str                                   # CARGO_PKG_VERSION
set_threads(n: int) -> int                         # size the global rayon pool once; returns size in effect
    # ValueError: n outside 1..=256. First call wins; if the pool already started, its size stays.
sec_to_frame(seconds: float, fps: int) -> int      # round half away from zero, clamped >= 0
    # ValueError: seconds not finite, fps outside 1..=99, result >= 2^40
blake3_bytes(data: bytes) -> str                   # hex digest; GIL released
blake3_files(paths: Iterable[str | os.PathLike]) -> list[str]   # parallel, GIL released; order = input order
    # FileNotFoundError "<path>: ..." for a missing file; OSError for other I/O (e.g. a directory);
    # TypeError for a single str/Path (must be an iterable of paths)
compile_ass(words: list[tuple[str, int, int, bool]], width: int, height: int, fps: int,
            style: dict | None = None,
            y_overrides: list[tuple[int, int, float]] | None = None)
    -> tuple[str, list[tuple[int, int, str, float, float, float, float]]]
    # words: (text, start_frame, end_frame, highlight); returns (ass_text, events)
    # event: (start, end, text, font_size, est_width, x, y); event text is the raw (unescaped) word text
    # ValueError: fps outside 1..=99; width or height 0; word not 0 <= start < end < 2^40; words not sorted
    #   by start; unknown style key; style rule broken (see .ass rules); y_override y not finite or start > end
caption_slots() -> list[tuple[str, float]]         # [("lower",0.70),("mid",0.50),("upper",0.24)] fraction of height
camera_presets() -> list[str]                      # static, ken_burns_zoom_in/out, pan_left/right/up/down
camera_plan(preset: str, intensity: float, src_w: float, src_h: float, out_w: float, out_h: float,
            focus_box: list[float] | None = None) -> tuple[W4, W4, float]   # (start_win, end_win, motion_px)
    # focus_box = [ymin, xmin, ymax, xmax] on a 0..=1000 grid
    # ValueError: unknown preset; intensity not finite or < 0; any size not finite or <= 0;
    #   focus_box value not finite / outside 0..=1000 / ymin >= ymax / xmin >= xmax
camera_windows(start: W4, end: W4, n: int) -> list[W4]   # n windows, log-space zoom, glide ease, pivoted centre
    # ValueError: a window not finite or w/h <= 0; n > 2^24
warp_frame(src: bytes, src_w: int, src_h: int, win: W4, out_w: int, out_h: int) -> bytes   # GIL released
    # ValueError: any size 0 or > 32768; buffer > 2^31; len(src) != src_w*src_h*3; bad window.
    # A window outside the source is clamped to the edge pixels (not an error).
frame_stats(frames: Iterable[bytes], width: int, height: int)
    -> list[tuple[float, float, list[float], list[float]]]  # (mean_luma, black_frac, std[3], detail[3]); GIL released
frame_motion(frames: Iterable[bytes], width: int, height: int) -> list[float]   # len = len(frames)-1; GIL released
    # both: ValueError if width < 4 or height < 4, or a frame length != width*height*3.
    # bytes are borrowed zero-copy; other bytes-like objects (bytearray) are copied.
make_proxy(src, out, height=360, fps=5, start_s=0.0, seconds=None, crf=30, threads=2, ffmpeg="ffmpeg") -> Path
    # ffmpeg spawned from Rust, GIL released; <out>.part renamed on success, removed on failure
    # ValueError: height odd / outside 64..=1080, fps outside 1..=30, start_s < 0 or not finite, seconds <= 0,
    #   crf > 51, threads outside 1..=16. FileNotFoundError: src missing. OSError: spawn failure or ffmpeg exit != 0
detect_pauses_pcm(pcm: bytes, rate: int, fps: int, threshold_db=None, min_frames=6)
    -> tuple[list[tuple[int, int, float]], float]   # ([(start_frame, end_frame, mean_db)], threshold_db used)
    # pcm = mono s16le. threshold None = adaptive. ValueError: odd byte length, rate outside 1000..=192000,
    #   fps outside 1..=99, min_frames < 1, threshold not finite
detect_pauses_file(path, fps, threshold_db=None, min_frames=6, rate=16000, ffmpeg="ffmpeg")
    -> tuple[list[tuple[int, int, float]], float, int]   # (pauses, threshold_db, total_frames); GIL released
    # FileNotFoundError: path missing. OSError: ffmpeg failure. ValueError: as detect_pauses_pcm
suggest_boundaries(pauses, total: int, min_len: int, max_len: int) -> list[int]   # interior cut frames
    # cut = midpoint of the longest pause whose midpoint is in [prev+min_len, prev+max_len] and leaves >= min_len;
    # none -> forced cut at prev+max_len; tail < min_len merges into the previous segment;
    # pauses touching frame 0 or total are never cuts. ValueError: total < 1, min_len < 1, max_len < min_len
```

Adding or renaming a function: register it in `#[pymodule] fn hypoverum_core_rs`, update this block and `hypoverum_core_rs.pyi`, update callers in `video_engine/`.

## Build and test

Environment (every shell):

```
source ~/.cargo/env
export CC=/usr/bin/gcc                 # ~/.local/bin/cc is NOT a compiler; ~/.cargo/config.toml sets linker=/usr/bin/gcc
export PYO3_PYTHON=/workspace/engine-venv/bin/python
cd /workspace/video-engine/rust/hypoverum_core_rs
```

| Step | Command |
|---|---|
| Type check | `cargo check -j 4` |
| Format | `cargo fmt --check` (fix: `cargo fmt`) |
| Lint | `cargo clippy -j 4 --all-targets -- -D warnings` (with `-W clippy::pedantic` for strict lint checks) |
| Rust tests | `TMPDIR=/dev/shm/video_engine/tmp cargo test -j 4 --no-default-features` (Linux; on macOS use any writable `TMPDIR`; tests write temp files under `TMPDIR`; `proxy_and_pcm_roundtrip` needs ffmpeg on PATH and skips without it) |
| Install into venv | `source /workspace/engine-venv/bin/activate && maturin develop --release` |
| Python tests | `cd /workspace/video-engine && /workspace/engine-venv/bin/python -m pytest -q` |

Facts:
- `cargo test` with default features fails to link (`cannot find -lpython3.13`): the system Python has no shared libpython.
  Run Rust tests with `--no-default-features`; keep logic out of `mod py` so it stays testable.
- `maturin develop` installs an editable package into `/workspace/engine-venv` (`site-packages/hypoverum_core_rs/hypoverum_core_rs.abi3.so`, `__init__.pyi`, `py.typed`).
  Python imports the installed `.so`; source edits have no effect until rebuilt.
- Use `--release` for installs: debug builds are much slower in warp/pixel code and panic on integer overflow.
- When another agent may be building, use a private target dir: `CARGO_TARGET_DIR=/tmp/<name>-target`.
- Scratch build without touching the venv: `cargo build --release --features pyo3/extension-module`, copy `target/release/libhypoverum_core_rs.so` to `<dir>/hypoverum_core_rs.abi3.so`, run with `PYTHONPATH=<dir>`.
- Keep `-j 4`.

## PyO3 rules

- API era: PyO3 0.27. Use `Bound<'py, T>`, `py.detach(..)` (not `allow_threads`), `FromPyObjectOwned`, `PyBytes::new`. No GIL Refs, no `&PyAny`.
- No panic may cross the boundary.
  A Rust panic becomes `pyo3_runtime.PanicException`, which derives from `BaseException`, so `except Exception` does not catch it.
  Forbidden on user-controlled data: `unwrap`, `expect`, slice indexing without a prior bounds check, `f64::clamp` with possibly-NaN bounds, `par_chunks_mut(0)`, unchecked `*`/`+` on sizes or frames.
- Validation lives in the core modules (so `cargo test --no-default-features` covers it);
    every core entry point returns `Result<_, String>` and every `#[pyfunction]` that takes caller data returns `PyResult`:
  - sizes: `num::rgb_len` (each side `1..=MAX_SIDE`, `checked_mul`, total `<= MAX_BUF`), buffer length equals `w*h*3`;
  - floats: `num::finite` / `num::positive`; clamp with `num::clampf`; convert with `num::f64_to_i64` / `f64_to_index` (never `as`);
  - fps: `frames::check_fps` (`1..=99`);
  - frames: `ass::check_words` (`0 <= start < end < MAX_FRAME`, sorted by start); frame arithmetic uses `checked_*` / `saturating_*`.
- Error mapping: invalid argument -> `PyValueError`;
    missing file -> `PyFileNotFoundError`;
    other filesystem -> `PyIOError`, path in the message;
    wrong container type -> `PyTypeError`.
  `lib.rs` maps core `String` errors to `PyValueError`.
- Release the GIL with `py.detach` around any work that can exceed ~1 ms: hashing, rayon loops, pixel work, I/O.
  Copy or borrow inputs before `detach`; build Python objects after it.
- Rayon uses the global pool.
  `video_engine/config.py` sets `RAYON_NUM_THREADS` and calls `set_threads(RUST_THREADS)` at import, before any parallel call.
  `RUST_THREADS` = env `VIDEO_ENGINE_RUST_THREADS`, default `max(1, THREADS - 2)` (headroom for concurrent ffmpeg).
  Python may call several detached functions concurrently from threads; do not create per-call pools.
- File hashing (`hash_file`) reads 4 MiB chunks (`update_rayon` for chunks >= 1 MiB).
  No mmap: a file truncated while hashed yields a normal result or `OSError`, never SIGBUS. blake3 is built without the `mmap` feature.
- Type mapping: `int` <-> `i64` (frames) / `u32` (width, height, fps) / `usize` (pixel dims, counts);
    `float` <-> `f64`;
    `bytes` -> `&[u8]` (borrowed, zero-copy) or `Vec<u8>` (copied);
    `bytes` out via `PyBytes::new`;
    `tuple` <-> Rust tuple;
    `list` <-> `Vec`;
    `dict` -> `&Bound<PyDict>`;
    `None` <-> `Option`.
  Paths are extracted as `PathBuf`, so `str` and `pathlib.Path` (any `os.PathLike`) both work.
  Frame iterables are taken as `&Bound<PyAny>` and borrowed (no owned `Vec<Vec<u8>>` copy).
- Style dicts: unknown keys raise `ValueError` (the `known` list in `lib.rs::style_from`).
  Adding a `Style` field means updating `ass::Style`, `Default`, `known`, `style_from`, and `video_engine/captions.py` docs.
- No `unsafe`. The crate has none; keep it that way.

## Frame and time rules

- All engine times are integer frames.
  `sec_to_frame` (Rust, called by `video_engine.timeline.sec_to_frame`) is the only seconds->frames conversion;
    it rounds half away from zero (`2.5 s @ 1 fps -> 3`, unlike Python `round`) and clamps negatives to 0.
- `fps` is an integer (`u32`) in `1..=99`, enforced by `sec_to_frame` and `compile_ass` (and `Settings.fps` in the schema).
  At `fps >= 100` two frames would share a centisecond.
  Fractional rates (29.97) are not represented.
- Frame ranges are half-open `[start, end)`: `end` is the first frame not shown. Duration = `end - start`. `y_overrides` ranges use the same convention.
- .ass time = `frame_to_cs(frame, fps) = floor(frame*100/fps)` centiseconds.
  For `fps < 100` this shows an event exactly from frame `start` through frame `end-1`.
  Consecutive events share the boundary value (`end_i == start_{i+1}`): no gap, no overlap.
  Never round to nearest; never subtract 1 cs from ends.
- `frame_to_cs` is integer-only: `frame.checked_mul(100)? / fps`; negative frames are an error. No floats in frame->cs.
- Caption hold (`end + hold_frames`) saturates and is capped at `MAX_FRAME`.
- Schema mirror (`video_engine/timeline.py`): `CaptionWord` requires `0 <= start_frame < end_frame < 2^40`; `Timeline.captions` must be sorted by `start_frame`.
  STT words are sorted in `audio.words_to_captions` before compile.

## .ass rules

- Header: `PlayResX = width`, `PlayResY = height` of the output video; `ScaledBorderAndShadow: yes`; `WrapStyle: 2` (no auto wrap); `YCbCr Matrix: TV.709`.
- One style `Cap`.
  Default: no backing box (`BorderStyle=1`), white bold Inter 84, outline 5 (`OutlineColour` `000000` alpha 0x20), `Alignment=5`, `MarginL/R = margin_x` (90).
- Soft shadow (default): style `Shadow=0`;
    a separate layer-0 Dialogue with invisible fill (`\1a&HFF&`), blurred translucent outline, offset by (`shadow_dx`, `shadow_dy`), default (`shadow`, 1.5 x `shadow`).
  Layer 1 is the visible text.
  Tests: `ass::tests::good_shadow_dx_dy_offset_the_soft_shadow_straight_down`, `good_default_soft_shadow_offset_unchanged`.
- `box_bg=true` -> `BorderStyle=3`. libass draws the box in `OutlineColour` and the box shadow in `BackColour`, so `OutlineColour = box_colour` (alpha 0x50) and `BackColour = outline_colour` (alpha `shadow_alpha`).
  Test: `ass::tests::box_colour_goes_to_outline_colour`.
- Colours in style: `&HAABBGGRR`; in override tags: `&HBBGGRR&`. Inputs are `RRGGBB` (optional `#`).
- Every caption is one line.
  Font size is reduced (`\fs`) so `est_width <= width - 2*margin_x`.
  Position is `\an5\pos(width/2, y)`, with `y = round(height * slot)` or a matching `y_overrides` value.
- Text escaping (`ass::esc`, applied to every caption word): `{` -> `(`, `}` -> `)`;
    `\` -> `＼` (U+FF3C; libass has no `\\` escape, so a literal backslash could form `\N`, `\n`, `\h`);
    every control character (`char::is_control`: CR, LF, tab, NUL, ESC, U+0085, ...) -> space.
  Result: one Dialogue line per layer per caption, no user-controlled override codes.
- Style rules (`Style::validate`, ValueError otherwise): `size > 0`, `emphasis_scale > 0`, `outline >= 0`, `shadow_blur >= 0`, `shadow` finite, `shadow_dx` / `shadow_dy` finite when set, `0 <= margin_x < width/2`, `max_words >= 1`, `max_chars >= 1`, `gap_break_frames >= 0`, `hold_frames >= 0`, `font` non-empty without `,` or control characters, slot in `lower|mid|upper`, colours `RRGGBB`.
- Burning (`audio.composite` via `audio.subtitle_filter`): ffmpeg runs with `cwd` = the .ass folder;
    the filter is the fixed `subtitles=filename=captions.ass:fontsdir=fonts` (`fonts` = symlink to `config.FONTS_DIR`).
  No path is escaped into the filter graph; every other path given to that ffmpeg call is absolute.
- Output is UTF-8; Python writes it with `encoding="utf-8"`.
  Width estimate and `max_chars` count Unicode scalar values (`chars()`), not bytes.
  `uppercase` uses Unicode `to_uppercase`.
- Grouping: <= `max_words` words and <= `max_chars` chars per caption;
    split at gaps > `gap_break_frames` and after phrase punctuation `.,!?;:…`;
    each caption is held `hold_frames` longer but never past the next caption start.

## Cache-key rules

Cache keys are built in Python: `blake3_json(dict)` = BLAKE3 of `json.dumps(sort_keys=True, separators=(",", ":"))`;
  file identity = `blake3_files` (full content hash, not size/mtime).

A key must include every input that changes the output:
- content hash of every source file read, including fonts, templates and JS libraries copied into the render;
- every parameter: frames (`in`, `n`), `W`, `H`, `fps`, camera move, encoder args (`config.X264`), model names, prompts;
- a stage version constant (`NATIVE_VERSION`, `HF_VERSION`, `MAP_VERSION`, `STT_VERSION`), bumped on any change in code that affects output;
- the content hash of the engine sources that shape the product (`util.code_hash`): native `native.CODE` (`native.py`, `overlays.py`), HyperFrames `hyperframes.CODE` (`hyperframes.py`; `legibility.py` through the scene), grade `grade.py`, audio pass `audio.py`.
  Gate (checker) modules are in no key: gate results are never cached;
- `core.ALGO_VERSION` (Rust `lib.rs::ALGO_VERSION`) for every key whose output depends on the crate: native segment key (`native.plan`, key `"algo"`), HyperFrames segment key (`hyperframes.cache_key`), map copy key (`hyperframes._map_copy`).
  Bump it in the same commit as any output-changing Rust edit (camera windows, warp, .ass text).
  Old cache entries then miss and are re-rendered; they are evicted by the cache size limit.
- HyperFrames builds also hash the fonts copied into the build dir (`hyperframes.HF_FONTS` plus the `hyperframes.THEME_FONTS` roles the scene theme uses, from `config.FONTS_DIR`).
- HyperFrames segment key (`hyperframes._key`): `HF_VERSION`, capture/encode settings (`HF_QUALITY`, `HF_CRF`, `config.X264`, normalising `config.x264_threads`), `core.ALGO_VERSION`, the scene (every profile value that reaches the template: `theme`, `print` with the map grade, `layout`, `legibility` with `legibility.code_hash()`; card images as `assets/card_<content hash>.<ext>`), the map derivative key, content hashes of the textures and fonts copied into the build dir, and `toolchain.hf_identity()`.
- Native segment key (`native.plan`): `NATIVE_VERSION`, source content hash, mode, `in`, `n`, camera move, `W`, `H`, `fps`, `config.X264`, `resources.X264_THREADS`, `config.X264_REPEATABLE`, `-version` output of `config.FFMPEG`, `native.CODE`, `core.ALGO_VERSION`, the copy layout in copy mode, drawn overlays (`overlays.key_part`: spec, font hashes, Pillow version), the native profile values.
- Grade key (`grade._key`): `GRADE_VERSION`, `grade.py`, rendered file content hash, frame count, baked LUT text per range, `config.X264`, x264 threads, `config.X264_REPEATABLE`, ffmpeg `-version`.
- Composite key (`audio.composite_key`) and audio key (`audio.audio_key`): `manifest.cmd_key` of the ffmpeg command (every input file as its sha256: segment files, transition clips, watermark PNG, `.ass`) plus the segment hashes and thread count;
    the composite adds every font file when captions are burned, the audio key adds the loudness-correction values and `audio.py`.
  Transition clips are drawn from both neighbouring segment files on every build and written bit-exact (`-fflags +bitexact`, no metadata), so equal frames give an equal clip hash.
- Words key (`audio.words_key`): `STT_VERSION`, voiceover content hash, language, model, keyterms.
- Map derivative key (`hyperframes.map_key`): `MAP_VERSION`, `core.ALGO_VERSION`, source map content hash, output width (after the `MAP_MAX_SIDE` cap), `MAP_MAX_SIDE`, resample filter, `MAP_JPEG_QUALITY`, Pillow version.
- DOM probe key (`hyperframes.probe_key`, cache kind `dom`, entry name `<segment key>.<probe key>`): `PROBE_VERSION`, `hyperframes.CODE`, `toolchain.hf_identity()`, every file of the built segment dir (relative path, content hash), the probe times (4 dp), viewport, screenshot flag and bg mode.
  It holds measurements only; gates judge them on every run.
- Toolchain identity (`toolchain.hf_identity()`): content of `vendor/VERSIONS.json`, the node binary, every file of the headless Chrome dir, every file of `vendor/npm/node_modules`, `vendor/js/gsap.min.js`, every file of `video_engine/template/`, `video_engine/hf/dom_probe.mjs`, and the `-version` output of `config.FFMPEG` and of the ffmpeg the HyperFrames CLI encodes with.
  Directory hashes use paths relative to the directory.
  Computed once per process; directory content hashes are memoised in `<cache>/toolchain/`, keyed by every file's path, size, mtime_ns, inode and device.

Do not put absolute paths or timestamps in keys: they change without changing output.

## Commit checklist (Rust changes)

1. `cargo fmt --check` clean.
2. `cargo clippy -j 4 --all-targets -- -D warnings` clean; no new `unwrap`/`expect`/indexing on user data; no `unsafe`.
3. `cargo test -j 4 --no-default-features` passes; new logic has a test in its module.
4. New or changed `#[pyfunction]`: validates inputs, returns `PyResult`, releases the GIL for heavy work, is registered in the module, and is listed in this file.
5. Output-changing change: bump `ALGO_VERSION` (Rust) and/or the stage constant(s) used in cache keys.
6. Signature change: update `hypoverum_core_rs.pyi` and the Python API block above.
7. `maturin develop --release` into `/workspace/engine-venv`.
8. `python -m pytest -q` in `/workspace/video-engine` passes (`tests/test_rust_core.py` holds the boundary regression cases).
