"""Explicit proxy encode quality: hermetic tests, no media renders or toolchain subprocesses."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import base_doc
from video_engine import assets, audio, batch, build, cli, hyperframes, manifest, native, profiles, timeline
from video_engine.util import EngineError


@pytest.fixture
def tl():
    doc = base_doc(Path("unused"))
    doc["profile_overrides"] = {"render": {
        "hf_quality": "high", "hf_crf": 10, "segment_crf": 12, "segment_preset": "veryslow",
        "segment_tune": "grain", "final_crf": 12, "final_preset": "veryslow", "final_tune": "grain",
        "final_maxrate_kbps": 40000, "final_bit_depth": 10, "map_jpeg_quality": 100,
    }}
    return timeline.Timeline.model_validate(doc)


def test_proxy_overrides_expensive_quality_but_not_geometry_or_source(tl):
    before, source = tl.model_dump(), profiles.of(tl).report()
    proxy = profiles.for_quality(tl, "proxy")
    r = profiles.of(proxy).render
    assert proxy is not tl and proxy.settings == tl.settings and proxy.segments == tl.segments
    assert r.hf_quality == "draft" and r.hf_crf == 28 and r.map_jpeg_quality == 85
    for stage in ("segment", "final"):
        args = r.x264(stage)
        assert args[args.index("-crf") + 1] == "28"
        assert args[args.index("-preset") + 1] == "ultrafast"
        assert "-tune" not in args and "-maxrate" not in args
    assert r.final_pix_fmt() == "yuv420p"
    assert tl.model_dump() == before and profiles.of(tl).report() == source
    assert profiles.for_quality(tl, "delivery") is tl
    assert profiles.of(proxy).blake3 != source["blake3"]
    assert profiles.of(profiles.for_quality(tl, "proxy")).blake3 == profiles.of(proxy).blake3
    q = profiles.quality_report(proxy)
    assert (q["width"], q["height"], q["fps"]) == (1080, 1920, 30)
    assert q["hf_quality"] == "draft" and q["final_x264"] == r.x264("final")


@pytest.mark.parametrize("kwargs, message", [
    ({"quality": "proxy"}, "requires --mode draft"),
    ({"quality": "unknown", "mode": "draft"}, "unknown render quality"),
    ({"quality": "proxy", "mode": "draft", "repin": True}, "cannot --repin"),
])
def test_api_rejects_invalid_quality_before_loading(tmp_path, monkeypatch, kwargs, message):
    monkeypatch.setattr(build.timeline, "load", lambda p: pytest.fail("must validate before loading"))
    with pytest.raises(EngineError, match=message):
        build.build(tmp_path, **kwargs)
    assert not (tmp_path / "out").exists()


def test_proxy_cannot_target_delivery_or_escape_draft(tmp_path):
    for out in (tmp_path / "out" / "final.mp4", tmp_path / "elsewhere.mp4"):
        with pytest.raises(EngineError, match="inside.*out/draft"):
            build.build(tmp_path, mode="draft", quality="proxy", out=out)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "draft").symlink_to(tmp_path / "out", target_is_directory=True)
    with pytest.raises(EngineError, match="inside.*out/draft"):
        build.build(tmp_path, mode="draft", quality="proxy")


@pytest.mark.parametrize("command", ["build", "render", "batch"])
def test_cli_rejects_release_proxy_without_build_or_cache_clear(tmp_path, monkeypatch, command):
    monkeypatch.setattr(build, "build", lambda *a, **k: pytest.fail("must reject before build"))
    monkeypatch.setattr(build.cache, "clear", lambda: pytest.fail("must reject before clearing"))
    assert cli.main([command, str(tmp_path), "--quality", "proxy", "--no-cache"]) == 1
    assert not (tmp_path / "out").exists()


def test_cli_and_batch_forward_quality(tmp_path, monkeypatch):
    calls = []
    def fake(p, **kwargs):
        calls.append(kwargs)
        return build.BuildResult(None, 0, 0, mode=kwargs["mode"], quality=kwargs["quality"],
                                 timer=SimpleNamespace(rows=[]))
    monkeypatch.setattr(build, "build", fake)
    assert cli.main(["build", str(tmp_path), "--mode", "draft", "--quality", "proxy"]) == 0
    assert cli.main(["batch", str(tmp_path), "--mode", "draft", "--quality", "proxy"]) == 0
    assert len(calls) == 2 and all(c["mode"] == "draft" and c["quality"] == "proxy" for c in calls)
    assert cli.parser().parse_args(["build", "p", "--mode", "draft"]).quality == "delivery"


def test_proxy_encode_cache_keys_differ_and_delivery_stays_stable(tl, tmp_path, monkeypatch):
    proxy = profiles.for_quality(tl, "proxy")
    monkeypatch.setattr(native.toolchain, "ffmpeg_version", lambda *a: "test")
    monkeypatch.setattr(hyperframes.toolchain, "hf_identity", lambda: "test")
    monkeypatch.setattr(hyperframes, "font_files", lambda scene: [])
    job = native.NativeJob(tl.segments[0], tmp_path / "image.jpg", "camera", 1600, 1200)
    nkey = native._key(job, tl, "camera", "imagehash")
    assert nkey != native._key(job, proxy, "camera", "imagehash")
    assert nkey == native._key(job, tl, "camera", "imagehash")
    assert hyperframes._key({}, [], "mapkey", tl) != hyperframes._key({}, [], "mapkey", proxy)
    # Composite command includes the final encoder arguments; no actual video probe/render.
    monkeypatch.setattr(audio, "video_inputs", lambda *a: (["-i", "segment.mp4"], 1, "", "[0:v]"))
    from video_engine import watermark
    monkeypatch.setattr(watermark, "render", lambda *a: None)
    monkeypatch.setattr(audio.toolchain, "x264_pix_fmts", lambda *a: ["yuv420p10le"])
    out = tmp_path / "composite.mp4"
    def ckey(t):
        cmd, _, parts, scratch = audio.composite_cmd(t, [], None, out, 1)
        return audio.composite_key(cmd, parts, None, out, scratch[-1])
    key = ckey(tl)
    assert key != ckey(proxy) and key == ckey(tl)


@pytest.fixture
def mocked_pipeline(tmp_path, monkeypatch, tl):
    """Exercise real build publishing/report/manifest with fake media stages only."""
    (tmp_path / "master_timeline.json").write_text(tl.model_dump_json())
    monkeypatch.setattr(build.timeline, "load", lambda p: tl)
    monkeypatch.setattr(assets, "ensure_credits", lambda t, p: (t, []))
    monkeypatch.setattr(assets, "resolve_timeline_paths", lambda t, p: t)
    def captions(t, p, **kwargs):
        assert kwargs["pin"] is False
        return [], "stub"
    monkeypatch.setattr(audio, "captions_for", captions)
    rep = SimpleNamespace(ok=True, timings={}, subtimings={}, findings=[], fixes=[], ai=None,
                          caption_y_overrides=[], summary=lambda: "preflight OK")
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: rep)
    monkeypatch.setattr(build.sound_request, "ensure_sounds", lambda *a, **k: None)
    monkeypatch.setattr(build.mixer, "plan", lambda *a, **k: None)
    monkeypatch.setattr(manifest, "timeline_files", lambda *a: [])
    monkeypatch.setattr(manifest, "font_files", lambda: [])
    monkeypatch.setattr(manifest, "toolchain", lambda: {"test": True})
    loud = [{"I": -14, "TP": -1, "LRA": 2}]
    monkeypatch.setattr(audio, "cached_master", lambda *a, **k: (tmp_path / "audio", True, "audio-key", [], loud))
    monkeypatch.setattr(build, "render_segments", lambda *a, **k: [])
    monkeypatch.setattr(build.streams, "joinable", lambda *a: (True, []))
    monkeypatch.setattr(build.transitions, "plan_key", lambda *a: None)
    monkeypatch.setattr(audio, "composite", lambda *a, **k: (tmp_path / "video", False, "video-key"))
    monkeypatch.setattr(audio, "mux", lambda t, v, a, out: out.write_bytes(b"fake-video"))
    monkeypatch.setattr(build, "count_frames", lambda *a: tl.total_frames)
    monkeypatch.setattr(build.qa, "pixel_gates", lambda *a, **k: ([], {"motion": {}, "samples": 1}))
    monkeypatch.setattr(build.style, "silence_findings", lambda *a: [])
    monkeypatch.setattr(build.audio_source, "build_findings", lambda *a: [])
    monkeypatch.setattr(build, "video_info", lambda *a: {"stub": True})
    return tmp_path


def test_proxy_build_reports_actual_settings_without_touching_source_or_delivery(mocked_pipeline, tl):
    p = mocked_pipeline
    out = p / "out"
    out.mkdir()
    names = ("final.mp4", "build_report.json", "build_manifest.json")
    for name in names:
        (out / name).write_bytes(b"original-delivery")
    before = (p / "master_timeline.json").read_bytes()
    original = tl.model_dump()
    result = build.build(p, mode="draft", quality="proxy")
    assert result.ok and result.quality == "proxy" and result.mode == "draft"
    assert result.final == out / "draft" / "final.draft.mp4"
    report = json.loads(result.report.read_text())
    man = manifest.load(out / "draft")
    for doc in (report, man):
        assert doc["quality"] == "proxy"
        assert doc["render_quality"] == profiles.quality_report(profiles.for_quality(tl, "proxy"))
        assert doc["source_profile"] == profiles.of(tl).report()
        assert doc["profile"]["blake3"] != doc["source_profile"]["blake3"]
    assert (p / "master_timeline.json").read_bytes() == before and tl.model_dump() == original
    assert all((out / name).read_bytes() == b"original-delivery" for name in names)


def test_draft_alone_and_later_release_keep_delivery_quality(mocked_pipeline, tl):
    p = mocked_pipeline
    source = profiles.of(tl).report()
    build.build(p, mode="draft", quality="proxy")
    for mode in ("draft", "release"):
        result = build.build(p, mode=mode, pin_words=False)
        report = json.loads(result.report.read_text())
        assert result.quality == "delivery" and report["quality"] == "delivery"
        assert report["profile"] == source == report["source_profile"]
        assert report["render_quality"]["hf_quality"] == "high"
        assert report["render_quality"]["final_pix_fmt"] == "yuv420p10le"


@pytest.mark.parametrize("prepared", [False, True])
def test_proxy_never_appends_missing_sound_credit(mocked_pipeline, monkeypatch, prepared):
    p = mocked_pipeline
    credit = "song-id — Artist — Song — licensed source"
    provenance = {"source": "epidemic", "id": "song-id", "title": "Song", "artist": "Artist", "credit": credit}
    mix = SimpleNamespace(sounds={}, music=SimpleNamespace(provenance=provenance), kinds={}, report=lambda: {})
    monkeypatch.setattr(build.mixer, "plan", lambda *a, **k: mix)
    monkeypatch.setattr(manifest, "sounds_entry", lambda *a, **k: {})
    monkeypatch.setattr(build.sound_cache, "append_credits", lambda *a: pytest.fail("must not modify source credits"))
    monkeypatch.setattr(build.sound_request, "ensure_sounds", lambda *a, **k: pytest.fail("must not mutate sound handoff"))
    source = p / "credits.txt"
    original = "Original visual credits\n" + (f"- {credit}\n" if prepared else "")
    source.write_text(original)
    timeline_bytes = (p / "master_timeline.json").read_bytes()
    if prepared:
        assert build.build(p, mode="draft", quality="proxy").ok
    else:
        monkeypatch.setattr(build, "render_segments", lambda *a, **k: pytest.fail("fail before render"))
        with pytest.raises(EngineError, match="cannot append missing sound credits.*--credits"):
            build.build(p, mode="draft", quality="proxy")
    assert source.read_text() == original
    assert (p / "master_timeline.json").read_bytes() == timeline_bytes


def test_proxy_missing_sound_does_not_mutate_request_bundle(mocked_pipeline, monkeypatch):
    p = mocked_pipeline
    bundle = p / "out" / "sound_request"
    bundle.mkdir(parents=True)
    response = bundle / "response.json"
    response.write_bytes(b"existing response must survive")
    monkeypatch.setattr(build.sound_cache, "missing", lambda *a: [{"slot": "music-test"}])
    monkeypatch.setattr(build.sound_request, "ensure_sounds", lambda *a, **k: pytest.fail("no import/delete/rewrite"))
    monkeypatch.setattr(build, "render_segments", lambda *a, **k: pytest.fail("fail before render"))
    with pytest.raises(EngineError, match="requires prepared sounds.*--credits"):
        build.build(p, mode="draft", quality="proxy", resume=True)
    assert response.read_bytes() == b"existing response must survive"


def test_failed_proxy_keeps_quality_provenance_and_delivery(mocked_pipeline, monkeypatch):
    p = mocked_pipeline
    def fail(*a, **k):
        raise EngineError("synthetic stage failure")
    monkeypatch.setattr(build, "render_segments", fail)
    with pytest.raises(EngineError, match="synthetic"):
        build.build(p, mode="draft", quality="proxy")
    report = json.loads((p / "out" / "draft" / build.FAILED_REPORT).read_text())
    assert report["quality"] == "proxy" and report["render_quality"]["hf_quality"] == "draft"
    assert not (p / "out" / "final.mp4").exists()
