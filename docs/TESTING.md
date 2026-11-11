# Tests

How to run the tests and what they cover.

**What this document answers**

- Which commands run which tests, and how long the full suite takes.
- Which tests need the HyperFrames toolchain and what happens without it.
- Where test scratch files live.
- How tests are named and what each gate must have.

## Running

```bash
pytest -q tests/test_<area>.py     # one area
pytest -q -n 4                     # everything in parallel (pytest-xdist); about 8 minutes with Chrome installed on 4 cores
pytest -q                          # everything, one process
```

The suite has about 1700 tests.
The Rust crate has its own: `cd rust/hypoverum_core_rs && cargo test --release --no-default-features` (49 tests; set `TMPDIR` to a scratch folder: `/dev/shm/video_engine/tmp` on Linux, any writable folder such as `$TMPDIR/video_engine/tmp` on macOS).
`python scripts/check_docs.py` checks the documentation (tests/test_check_docs.py runs it too).

## Creative policy and proxy regression checks

```bash
pytest -q tests/test_gate_intent.py tests/test_gate_severity.py tests/test_proxy_quality.py tests/test_creative_workflow.py
```

These cover local intent (including reason validation and segment isolation), hard black/decode checks, unknown vs static vs expected motion, insufficient evidence, required negative-space policy, default-off/explicit camera fixes, draft source preservation, proxy release/output rejection, resolved settings and visual cache separation. `test_build_publish.py` retains independent frame-count/decode/publication failure coverage.

## Toolchain-dependent tests

Tests that need the DOM probe or a HyperFrames render need `vendor/node` and `vendor/chrome` from `engine doctor --install`.
While the toolchain is missing they skip with that hint.
`VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1` makes them fail instead, as in the nightly CI job.
The same holds for a test that needs the open-assets flag library (`VIDEO_ENGINE_OPEN_ASSETS`, a shared folder outside the repository): it skips with that reason when the folder is absent and the engine reported it (`conftest.missing_open_assets`), and for a build test that stops at the engine's own "HyperFrames missing" error (`conftest.missing_hyperframes`).
A build test that fails because Node is missing only skips when the error reaches pytest; a CLI test that calls `cli.main` turns the error into exit code 1 and fails.
The two pixel-exact colour-grade checks (`test_golden_colour_grade`, `test_good_grade_intermediate_is_lossless`) need ffmpeg 7 or newer
(the `engine doctor` minimum) both on `PATH` and as `VIDEO_ENGINE_FFMPEG`, because ffmpeg 6 swscale rounds the grade's colour conversions
differently; with an older ffmpeg they skip with that reason (`conftest.require_ffmpeg_major`), and `VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1` makes them fail.
`tests/test_examples.py` needs network: `examples/minimal/prepare.sh` downloads OpenStreetMap tiles, and the tests skip only when that download fails. The tests set `PYTHON` to the test interpreter so prepare.sh uses the venv (which has Pillow), not a system `python3` that may not.

## Golden frames

`tests/test_golden_frames.py` renders a few small frames (216 x 384) of the paths that need no Chrome (native Ken Burns
over a still, a native footage pan, the crossfade, state, zoom whip and page turn transitions, a colour grade) and compares them with
the PNGs in `tests/golden/<platform>/`. A platform without a golden directory skips these cases instead of comparing
against another platform's pixels.
A case passes when the mean absolute difference is at most 1.5 levels and at most 0.5 % of the samples differ by more
than 24, so rounding differences between ffmpeg builds pass and a moved, re-coloured or re-timed picture fails.
On a failure the rendered frames are written next to the golden as `<name>.actual.png` (ignored by git).
After an intended visual change, refresh with `VIDEO_ENGINE_UPDATE_GOLDENS=1 pytest -q tests/test_golden_frames.py`
and look at the new PNGs before committing them.
HyperFrames scenes have no goldens yet: they need the vendored Chrome (a later step).

## Scratch files

All test scratch (scratch root, cache, generated assets, `tmp_path` base) lives in `/tmp/video_engine_test_shm/pytest-<pid>/`; override the parent with `VIDEO_ENGINE_SHM_TEST`.
It is removed when the session ends.
The parent is removed only when the session created it and it is empty.
A session removes only its own root, so simultaneous sessions do not interfere (`tests/test_scratch_cleanup.py`).

## Conventions

- Every gate has labelled GOOD and BAD cases (`test_good_...`, `test_bad_...`).
- `tests/test_gate_registry.py` fails when a gate id emitted by the code is not in `video_engine/gate_registry.py`, when a registered gate is emitted nowhere, or when the generated index in docs/GATES.md is stale.
- Caption against pin: `tests/test_gates_layer2.py::test_R63_bad_caption_too_close_to_pusat_kota_pin` and `..._good_caption_auto_placed_away_from_pin` cover the "Pusat Kota" pin case; `test_R63_dom_demo_end_to_end` runs the real DOM probe on a synthetic project with and without automatic placement.
- AI tests use mocks (Gemini client, OpenAI-compatible HTTP post) and agent bundles on disk; they use no network.
- A change to a keyed module must keep the cache-key tests (`tests/test_stage_keys.py`, `tests/test_code_hash.py`) true: a change of behaviour changes the key, a comment does not.
