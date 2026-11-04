"""Rust core hardening (docs/RUST.md audit): every bad input is a Python exception (never PanicException),
.ass text cannot inject events or override codes, ALGO_VERSION is part of every render cache key."""
import math
import os
import subprocess
import threading
from pathlib import Path

import pytest

import hypoverum_core_rs as core
from video_engine import audio, config, hyperframes, maps, native, timeline
from video_engine.util import EngineError

RGB2x2 = bytes(12)
WIN = (0.0, 0.0, 1.0, 1.0)
NAN, INF = float("nan"), float("inf")


def dialogues(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def style_fields(ass: str) -> list[str]:
    return next(line for line in ass.splitlines() if line.startswith("Style:")).split(",")


# --- finding 1: no panic across FFI -------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    (b"", 0, 0, WIN, 4, 4),                 # empty source, zero sizes (was: usize underflow panic)
    (RGB2x2, 2, 2, WIN, 0, 4),              # zero output width (was: chunk_size 0 panic)
    (RGB2x2, 2, 2, WIN, 4, 0),
    (RGB2x2, 2, 2, (0, 0, NAN, 1), 4, 4),   # NaN window
    (RGB2x2, 2, 2, (0, 0, 0, 1), 4, 4),     # zero-width window
    (RGB2x2[:-1], 2, 2, WIN, 4, 4),         # short buffer
    (RGB2x2, 2, 2, WIN, 1 << 40, 1 << 40),  # out_w*out_h*3 overflow
    (RGB2x2, 1 << 62, 1 << 62, WIN, 4, 4),  # src_w*src_h*3 overflow
])
def test_bad_warp_frame_raises_value_error(args):
    with pytest.raises(ValueError):
        core.warp_frame(*args)


def test_good_warp_frame_window_outside_source_is_clamped():
    out = core.warp_frame(bytes(range(12)), 2, 2, (-50.0, 80.0, 5.0, 5.0), 3, 3)
    assert len(out) == 27


@pytest.mark.parametrize("args", [
    ("ken_burns_zoom_in", 1.0, NAN, 1200.0, 1080.0, 1920.0),
    ("ken_burns_zoom_in", 1.0, 1600.0, INF, 1080.0, 1920.0),
    ("ken_burns_zoom_in", 1.0, 1600.0, 1200.0, 0.0, 1920.0),
    ("ken_burns_zoom_in", 1.0, 1600.0, 1200.0, 1080.0, 0.0),
    ("ken_burns_zoom_in", 1.0, 0.0, 1200.0, 1080.0, 1920.0),
    ("ken_burns_zoom_in", NAN, 1600.0, 1200.0, 1080.0, 1920.0),
    ("ken_burns_zoom_in", -1.0, 1600.0, 1200.0, 1080.0, 1920.0),
    ("no_such_preset", 1.0, 1600.0, 1200.0, 1080.0, 1920.0),
])
def test_bad_camera_plan_raises_value_error(args):
    with pytest.raises(ValueError):
        core.camera_plan(*args, None)


def test_good_camera_plan_all_presets_extreme_intensity():
    for p in core.camera_presets():
        for i in (0.0, 6.0, 1e300):
            a, b, m = core.camera_plan(p, i, 1600.0, 1200.0, 1080.0, 1920.0, None)
            assert all(math.isfinite(v) for v in (*a, *b, m)) and a[2] > 0 and b[2] > 0


# --- findings 2 & 4: .ass escaping ---------------------------------------------------------------------------

@pytest.mark.parametrize("ctrl", ["\n", "\r", "\r\n", "\t", "\x00", "\x1b", "\x85"])
def test_bad_control_chars_cannot_inject_events(ctrl):
    evil = f"a{ctrl}Dialogue: 9,0:00:00.00,9:00:00.00,Cap,,0,0,0,,PWNED"
    ass, ev = core.compile_ass([(evil, 0, 30, False)], 1080, 1920, 30, None, None)
    assert len(dialogues(ass)) == 2 and len(ev) == 1     # soft-shadow + text, nothing injected
    assert not any(line.startswith("Dialogue: 9") for line in ass.splitlines())
    body = ass.split("[Events]")[1]
    assert all(c not in body for c in ctrl.replace("\n", ""))   # only the file's own LF line ends remain


@pytest.mark.parametrize("text", [r"a\Nb", r"a\nb", r"a\hb", "a\\", r"\\N", r"{\b1}x"])
def test_bad_backslash_cannot_form_override_codes(text):
    ass, ev = core.compile_ass([(text, 0, 30, False)], 1080, 1920, 30, None, None)
    for line in dialogues(ass):
        visible = line.split(",,", 1)[1].rsplit("}", 1)[-1]   # text after the last override block
        assert "\\" not in visible and "{" not in visible
    if "\\" in text:
        assert "\uff3c" in dialogues(ass)[1]


# --- finding 3 + schema: frame validation --------------------------------------------------------------------

@pytest.mark.parametrize("words", [
    [("a", -1, 5, False)],
    [("a", 5, 5, False)],
    [("a", 9, 5, False)],
    [("a", 0, 1 << 40, False)],
    [("a", 0, 2**63 - 1, False)],
    [("b", 10, 20, False), ("a", 0, 5, False)],       # unsorted
])
def test_bad_compile_ass_frames_raise(words):
    with pytest.raises(ValueError):
        core.compile_ass(words, 1080, 1920, 30, None, None)


def test_good_compile_ass_large_frames_and_hold_saturate():
    _, ev = core.compile_ass([("a", (1 << 40) - 10, (1 << 40) - 1, False)], 1080, 1920, 30,
                             {"hold_frames": 2**62}, None)
    assert ev[0][1] == 1 << 40


@pytest.mark.parametrize("item", [
    {"word": "a", "start_frame": 5, "end_frame": 5},
    {"word": "a", "start_frame": 6, "end_frame": 5},
    {"word": "a", "start_frame": 0, "end_frame": 1 << 40},
    {"word": "a", "start_frame": -1, "end_frame": 3},
])
def test_bad_schema_caption_word(item):
    with pytest.raises(ValueError):
        timeline.CaptionWord(**item)


def test_bad_schema_captions_unsorted(project):
    def mut(doc):
        doc["captions"] = [{"word": "b", "start_frame": 10, "end_frame": 20},
                           {"word": "a", "start_frame": 0, "end_frame": 5}]
    with pytest.raises(EngineError, match="sorted"):
        project(mut)


def test_good_schema_captions_sorted(project):
    def mut(doc):
        doc["captions"] = [{"word": "a", "start_frame": 0, "end_frame": 5},
                           {"word": "b", "start_frame": 0, "end_frame": 9}]
    _, tl = project(mut)
    assert len(tl.captions) == 2


def test_good_stt_words_sorted_before_compile():
    caps = audio.words_to_captions([{"word": "b", "start": 1.0, "end": 1.2}, {"word": "a", "start": 0.0, "end": 0.0}],
                                   30, 300)
    assert [c.word for c in caps] == ["a", "b"] and all(c.end_frame > c.start_frame for c in caps)


# --- finding 5: fps 1..=99, integer centiseconds -------------------------------------------------------------

@pytest.mark.parametrize("fps", [0, 100, 120])
def test_bad_fps(fps):
    with pytest.raises(ValueError):
        core.compile_ass([("a", 0, 10, False)], 1080, 1920, fps, None, None)
    with pytest.raises(ValueError):
        core.sec_to_frame(1.0, fps)


@pytest.mark.parametrize("fps", [1, 24, 25, 30, 60, 99])
def test_good_fps_ass_time_is_floor_of_frame(fps):
    ass, _ = core.compile_ass([("a", 7, 8, False)], 1080, 1920, fps, None, None)
    cs = 7 * 100 // fps
    assert f"0:00:{cs // 100:02d}.{cs % 100:02d}" in dialogues(ass)[0]


# --- finding 6: style validation (defaults unchanged) --------------------------------------------------------

@pytest.mark.parametrize("style", [
    {"size": 0}, {"size": -84}, {"size": NAN}, {"emphasis_scale": 0}, {"margin_x": -1}, {"margin_x": 540},
    {"outline": -1}, {"font": "Inter,Bold"}, {"font": "Inter\nStyle: X"}, {"font": ""}, {"max_words": 0},
    {"hold_frames": -1}, {"primary": "white"},
])
def test_bad_style_raises(style):
    with pytest.raises(ValueError):
        core.compile_ass([("a", 0, 10, False)], 1080, 1920, 30, style, None)


@pytest.mark.parametrize("w, h", [(0, 1920), (1080, 0)])
def test_bad_video_size_raises(w, h):
    with pytest.raises((ValueError, OverflowError)):
        core.compile_ass([("a", 0, 10, False)], w, h, 30, None, None)


def test_good_default_style_still_84_outline_5():
    f = style_fields(core.compile_ass([("a", 0, 10, False)], 1080, 1920, 30, None, None)[0])
    assert (f[2], f[16]) == ("84", "5")


# --- finding 7: box_bg colour in OutlineColour ---------------------------------------------------------------

def test_good_box_bg_colour_in_outline_colour():
    f = style_fields(core.compile_ass([("a", 0, 10, False)], 1080, 1920, 30,
                                      {"box_bg": True, "box_colour": "112233", "outline_colour": "000000"}, None)[0])
    assert f[15] == "3" and f[5] == "&H50332211" and f[6].endswith("000000")


# --- finding 8: focus_box ------------------------------------------------------------------------------------

@pytest.mark.parametrize("fb", [[NAN, 0, 10, 10], [0, 0, INF, 10], [-1, 0, 10, 10], [0, 0, 1001, 10],
                                [10, 0, 5, 10], [0, 10, 10, 10]])
def test_bad_focus_box(fb):
    with pytest.raises(ValueError):
        core.camera_plan("ken_burns_zoom_in", 1.0, 1600.0, 1200.0, 1080.0, 1920.0, fb)
    with pytest.raises(ValueError):
        timeline.CameraMove(focus_box=fb)


def test_good_focus_box():
    core.camera_plan("ken_burns_zoom_in", 1.0, 1600.0, 1200.0, 1080.0, 1920.0, [0, 0, 1000, 1000])
    timeline.CameraMove(focus_box=[100, 200, 600, 900])


# --- finding 9: ALGO_VERSION and fonts in cache keys ---------------------------------------------------------

def test_good_algo_version_exported():
    assert isinstance(core.ALGO_VERSION, str) and core.ALGO_VERSION


def test_good_native_key_includes_algo_version(project, monkeypatch):
    p, tl = project()
    k1 = native.plan(tl.segments[0], tl, p).key
    monkeypatch.setattr(core, "ALGO_VERSION", core.ALGO_VERSION + "-test")
    assert native.plan(tl.segments[0], tl, p).key != k1


def test_good_hf_and_map_keys_include_algo_and_fonts(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    seg = tl.segments[2]
    _, scene = hyperframes.build_dir(seg, tl, p, tmp_path / "hf")
    k1 = hyperframes.cache_key(seg, tl, p, scene)
    m1 = maps._map_copy(p / "assets" / "map.jpg", 500)
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for f in hyperframes.HF_FONTS:
        (fonts / f).write_bytes((config.FONTS_DIR / f).read_bytes() + b"\0")
    monkeypatch.setattr(config, "FONTS_DIR", fonts)
    k2 = hyperframes.cache_key(seg, tl, p, scene)
    monkeypatch.setattr(core, "ALGO_VERSION", core.ALGO_VERSION + "-test")
    k3 = hyperframes.cache_key(seg, tl, p, scene)
    m2 = maps._map_copy(p / "assets" / "map.jpg", 500)
    assert len({k1, k2, k3}) == 3 and m1 != m2


# --- finding 10: hashing, GIL, threads -----------------------------------------------------------------------

def test_good_set_threads():
    n = core.set_threads(4)
    assert n >= 1 and core.set_threads(2) == n == config.RUST_THREADS_ACTIVE   # first call wins
    assert config.RUST_THREADS == max(1, config.THREADS - 2) or "VIDEO_ENGINE_RUST_THREADS" in os.environ


@pytest.mark.parametrize("n", [0, 257])
def test_bad_set_threads(n):
    with pytest.raises(ValueError):
        core.set_threads(n)


def test_good_chunked_file_hash_matches_bytes(tmp_path):
    sizes = [0, 1, (4 << 20) - 1, 4 << 20, (4 << 20) + 1, (9 << 20) + 7]
    paths = []
    for n in sizes:
        f = tmp_path / f"f{n}"
        f.write_bytes(bytes(i % 251 for i in range(n)) if n < 4096 else (b"abcdefg" * (n // 7 + 1))[:n])
        paths.append(f)
    assert core.blake3_files(paths) == [core.blake3_bytes(f.read_bytes()) for f in paths]


def test_good_blake3_bytes_concurrent_threads():
    data = b"x" * (8 << 20)
    want = core.blake3_bytes(data)
    out = []
    ts = [threading.Thread(target=lambda: out.append(core.blake3_bytes(data))) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == [want] * 4


# --- finding 11: input types and error classes ---------------------------------------------------------------

def test_good_blake3_files_accepts_pathlib(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"abc")
    assert core.blake3_files([f]) == core.blake3_files([str(f)]) == [core.blake3_bytes(b"abc")]
    assert core.blake3_files(iter([f])) == core.blake3_files((f,))


def test_bad_blake3_files_missing_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        core.blake3_files([tmp_path / "missing"])
    with pytest.raises(TypeError):
        core.blake3_files(tmp_path / "a.txt")   # a single path is not an iterable of paths
    with pytest.raises(OSError):   # a directory: OSError, not a panic
        core.blake3_files([tmp_path])


@pytest.mark.parametrize("w, h", [(3, 3), (1, 1), (0, 0), (3, 100)])
def test_bad_frame_stats_tiny_frames(w, h):
    with pytest.raises(ValueError):
        core.frame_stats([bytes(w * h * 3)], w, h)
    with pytest.raises(ValueError):
        core.frame_motion([bytes(w * h * 3)] * 2, w, h)


def test_good_frame_stats_4x4_and_bytearray():
    assert len(core.frame_stats([bytes(48), bytearray(48)], 4, 4)) == 2
    with pytest.raises(ValueError):
        core.frame_stats([bytes(47)], 4, 4)


@pytest.mark.parametrize("start", [(0, 0, 0, 1), (0, 0, -5, 1), (0, 0, 1, 0), (NAN, 0, 1, 1)])
def test_bad_camera_windows(start):
    with pytest.raises(ValueError):
        core.camera_windows(start, (0, 0, 1, 1), 3)


@pytest.mark.parametrize("sec", [NAN, INF, -INF, 1e30])
def test_bad_sec_to_frame(sec):
    with pytest.raises(ValueError):
        core.sec_to_frame(sec, 30)
    with pytest.raises(ValueError):
        timeline.sec_to_frame(sec, 30)


# --- finding 12: ffmpeg handling -----------------------------------------------------------------------------

def test_bad_decode_failure_includes_decoder_stderr(project, tmp_path):
    p, tl = project()
    job = native.plan(tl.segments[0], tl, p)
    bad = tmp_path / "corrupt.jpg"
    bad.write_bytes(b"not an image at all" * 10)
    job.src = bad
    with pytest.raises(EngineError) as e:
        native.render_camera(job, tl, tmp_path / "o.mp4", 2)
    msg = str(e.value)
    assert "could not decode" in msg and len(msg.split(": ", 2)[-1]) > 10 and "no stderr" not in msg


def test_good_subtitle_burn_path_with_special_chars(tmp_path):
    d = tmp_path / "we:ird, 'dir' [x]"
    d.mkdir()
    ass = d / "captions.ass"
    ass.write_text(core.compile_ass([("Halo", 0, 10, False)], 320, 240, 10, {"margin_x": 20, "size": 30}, None)[0])
    vf, cwd = audio.subtitle_filter(ass)
    assert str(d) not in vf and cwd == d.resolve()
    out = tmp_path / "out.mp4"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10:d=1",
                    "-vf", vf, "-frames:v", "5", str(out)], cwd=cwd, check=True, capture_output=True)
    assert out.stat().st_size > 0
    assert audio.subtitle_filter(ass) == (vf, cwd)   # idempotent (fonts symlink reused)


# --- finding 13: type stubs ----------------------------------------------------------------------------------

def test_good_type_stubs_ship_with_module():
    pkg = Path(core.__file__).parent
    stub = pkg / "__init__.pyi"
    assert stub.exists() and (pkg / "py.typed").exists()
    text = stub.read_text()
    for name in ("ALGO_VERSION", "set_threads", "compile_ass", "warp_frame", "blake3_files", "frame_stats"):
        assert name in text


def test_good_concurrent_same_key_cache_writes(tmp_path, monkeypatch):
    """Two segments sharing one map write the same cache key at once; neither writer may lose its temp file."""
    from video_engine import cache
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    src = tmp_path / "src.bin"
    src.write_bytes(os.urandom(1 << 20))
    errs = []

    def put():
        try:
            for _ in range(20):
                cache.put_file("maps", "samekey", src, ".jpg")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=put) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert (tmp_path / "cache" / "maps" / "samekey.jpg").read_bytes() == src.read_bytes()
    assert not list((tmp_path / "cache" / "maps").glob("*.tmp"))


def test_bad_shared_temp_name_would_collide(tmp_path):
    from video_engine import cache
    names = set()

    def grab():
        names.add(cache.tmp_name(tmp_path / "k.jpg"))
    ts = [threading.Thread(target=grab) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(names) == 4
