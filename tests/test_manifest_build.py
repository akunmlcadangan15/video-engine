"""Locked build manifest, split visual/audio caches, deliver build_id check, determinism, worker budget and the
per-stage resource report. GOOD and BAD cases."""
import json
import subprocess

import pytest

from video_engine import audio, build, config, deliver, manifest, profiles, resources
from video_engine.gates.core import Finding
from video_engine.util import EngineError

from test_build_publish import _Rep


@pytest.fixture
def proj(project, monkeypatch):
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    return p


def stream_md5(f, sel):
    out = subprocess.run([config.FFMPEG, "-v", "error", "-i", f, "-map", f"0:{sel}", "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip()


def edit(p, fn):
    f = p / "master_timeline.json"
    d = json.loads(f.read_text())
    fn(d)
    f.write_text(json.dumps(d))


def rep(p):
    return json.loads((p / "out" / "build_report.json").read_text())


# ---------------------------------------------------------------------------------------------------------- manifest
def test_good_manifest_written_and_tied_to_the_report(proj):
    build.build(proj)
    m = manifest.load(proj / "out")
    r = rep(proj)
    assert m["build_id"] == r["build_id"] and len(m["build_id"]) == 32
    assert {"ffmpeg", "x264", "node", "chrome", "hyperframes"} <= set(m["toolchain"])
    paths = {e["path"].rsplit("/", 1)[-1]: e["sha256"] for e in m["inputs"]}
    assert {"still.jpg", "clip_1080x1920_30.mp4", "map.jpg", "tone.wav"} <= set(paths)
    assert paths["still.jpg"] == manifest.sha256(proj / "assets" / "still.jpg")
    assert m["profile"]["blake3"] == profiles.load().blake3 and m["sounds"] == {"music": None, "sfx": {}}
    assert m["stage_keys"]["composite"] == r["stage_keys"]["composite"] and m["stage_keys"]["audio"]
    assert m["seeds"] == {"local_tone_click": 11, "local_tone_whoosh": 7, "local_tone_riser": 5}


def test_good_resource_report_per_stage(proj):
    build.build(proj)
    res = rep(proj)["resources"]
    assert res["budget"]["workers"] >= 2 and res["budget"]["x264_threads"] == 2
    stages = {s["stage"]: s for s in res["stages"]}
    assert {"segments", "composite", "audio", "mux", "qa"} <= set(stages)
    seg = stages["segments"]
    assert seg["wall_s"] > 0 and seg["cpu_s"] > 0 and seg["peak_rss_mb"] > 0
    assert seg["peak_rss_mb_by_kind"]["engine"] > 0


# ------------------------------------------------------------------------------------------------------ split caches
def test_good_audio_change_keeps_the_visual_caches(proj):
    build.build(proj)
    first = rep(proj)
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", proj / "assets" / "tone.wav", "-af", "volume=-6dB",
                    proj / "assets" / "tone2.wav"], check=True)
    edit(proj, lambda d: d["audio_chain"].update(voiceover_path="assets/tone2.wav"))
    build.build(proj)
    second = rep(proj)
    assert all(s["cache_hit"] for s in second["segments"])
    assert "(hit)" in dict((r[1], r[3]) for r in second["timings"])["composite"]
    assert "(hit)" not in dict((r[1], r[3]) for r in second["timings"])["audio"]
    assert second["stage_keys"]["composite"] == first["stage_keys"]["composite"]
    assert second["stage_keys"]["audio"] != first["stage_keys"]["audio"]


def test_bad_visual_change_reencodes_composite_but_not_audio(proj):
    build.build(proj)
    first = rep(proj)
    edit(proj, lambda d: d["segments"][0]["camera_move"].update(intensity=1.5))
    build.build(proj)
    second = rep(proj)
    t = dict((r[1], r[3]) for r in second["timings"])
    assert "(hit)" not in t["composite"] and "(hit)" in t["audio"]
    assert second["stage_keys"]["composite"] != first["stage_keys"]["composite"]
    assert [s["cache_hit"] for s in second["segments"]] == [False, True, True]


def test_good_two_uncached_builds_are_bit_identical(proj):
    build.build(proj, use_cache=False)
    f = proj / "out" / "final.mp4"
    a = (stream_md5(f, "v:0"), stream_md5(f, "a:0"))
    build.build(proj, use_cache=False)
    assert (stream_md5(f, "v:0"), stream_md5(f, "a:0")) == a


def test_good_split_passes_match_the_old_single_pass_streams(proj, tmp_path):
    """Composite (-an) + audio pass + stream copy give the same video and audio packets as one combined encode."""
    import shutil
    build.build(proj, use_cache=False, keep_workdir=True)
    try:
        _compare_split_and_single(proj, tmp_path)
    finally:
        shutil.rmtree(rep(proj)["workdir"], ignore_errors=True)


def _compare_split_and_single(proj, tmp_path):
    from video_engine import timeline
    tl = timeline.load(proj)
    segs = [s["path"] for s in rep(proj)["segments"]]
    out = tmp_path / "split.mp4"
    audio.final_mux(tl, proj, segs, None, out, threads=6)
    v = audio.composite_cmd(tl, segs, None, tmp_path / "x.mp4", 6)[0]
    a = audio.audio_cmd(tl, proj, tmp_path / "y.m4a", audio.measure_mix(tl, proj))
    one = tmp_path / "one.mp4"
    # combined single pass: video graph + audio graph in one ffmpeg run
    vi = v.index("-filter_complex")
    ai = a.index("-filter_complex")
    vin, ain = v[4:vi], a[4:ai]
    shift = sum(1 for x in vin if str(x) == "-i")
    agraph = a[ai + 1]
    for k in range(9, -1, -1):
        agraph = agraph.replace(f"[{k}:a]", f"[{k + shift}:a]")
    cmd = [*v[:4], *vin, *ain, "-filter_complex", v[vi + 1] + ";" + agraph, "-map", "[v]", "-map", "[a]",
           *config.X264, "-threads", "6", "-r", "30", "-frames:v", str(tl.total_frames), "-c:a", "aac", "-b:a",
           "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{tl.total_frames / 30:.6f}", one]
    subprocess.run([str(c) for c in cmd], check=True)
    assert stream_md5(out, "v:0") == stream_md5(one, "v:0")
    assert stream_md5(out, "a:0") == stream_md5(one, "a:0")


def test_good_audio_only_gain_pass(tmp_path, assets):
    src = tmp_path / "a.m4a"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", assets / "tone.wav", "-c:a", "aac", src], check=True)
    dst = audio.apply_gain(src, tmp_path / "b.m4a", 6.0, -1.5)
    assert audio.loudness(dst)["I"] > audio.loudness(src)["I"] + 4


# -------------------------------------------------------------------------------------------------------- sound lock
def _lock(tmp_path, tl_key, f, kinds=("pop",)):
    e = {"path": str(f), "sha256": manifest.sha256(f), "provenance": {"source": "epidemic", "id": "x"}}
    return {"sounds": {"music": None if tl_key is None else {**e, "brief_key": tl_key},
                       "sfx": {k: dict(e) for k in kinds}}}


def test_good_locked_sounds_reused_without_resolver(project, tmp_path):
    _, tl = project()
    f = tmp_path / "s.wav"
    f.write_bytes(b"RIFF....")
    got = manifest.locked_sounds(_lock(tmp_path, None, f), tl, ["pop"])
    assert got["music"] is None and got["sfx"]["pop"][0] == f


def test_bad_locked_sound_changed_names_relock(project, tmp_path):
    _, tl = project()
    f = tmp_path / "s.wav"
    f.write_bytes(b"RIFF....")
    lock = _lock(tmp_path, None, f)
    f.write_bytes(b"RIFF!!!!")
    with pytest.raises(EngineError, match="--relock"):
        manifest.locked_sounds(lock, tl, ["pop"])


def test_bad_lock_not_covering_the_timeline_is_ignored(project, tmp_path):
    _, tl = project()
    f = tmp_path / "s.wav"
    f.write_bytes(b"x")
    assert manifest.locked_sounds(_lock(tmp_path, None, f), tl, ["pop", "whoosh"]) is None   # new SFX kind
    assert manifest.locked_sounds(_lock(tmp_path, "other-brief", f), tl, ["pop"]) is None    # music brief changed
    assert manifest.locked_sounds(None, tl, ["pop"]) is None


def test_good_mixer_uses_the_lock(project, tmp_path, monkeypatch):
    from video_engine import mixer
    d, tl = project(lambda doc: doc.update(profile="geoverum"))
    f = tmp_path / "pop.wav"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=0.2", f], check=True)
    monkeypatch.setattr(mixer, "resolve_sfx", lambda *a: pytest.fail("resolver asked despite the lock"))
    monkeypatch.setattr(mixer, "resolve_music", lambda *a: pytest.fail("resolver asked despite the lock"))
    kinds = mixer.sfx.kinds_used(tl)
    lock = {"music": None, "sfx": {k: (f, {"source": "epidemic", "id": f"e:{k}"}) for k in kinds}}
    m = mixer.plan(tl, d, [], lock=lock)
    assert m.locked and set(m.kinds.values()) == set(kinds)


# ----------------------------------------------------------------------------------------------------------- deliver
def test_good_deliver_accepts_the_last_published_build(proj):
    build.build(proj)
    assert deliver.check_build(proj / "out" / "final.mp4")["build_id"] == manifest.load(proj / "out")["build_id"]


def test_bad_deliver_refuses_after_a_failed_build(proj, monkeypatch):
    build.build(proj)
    monkeypatch.setattr(build.audio, "loudness_findings", lambda m, *a: [Finding("A01", "fail", "injected")])
    with pytest.raises(EngineError):
        build.build(proj)
    with pytest.raises(EngineError, match="stale"):
        deliver.check_build(proj / "out" / "final.mp4")


def test_bad_deliver_refuses_a_build_id_mismatch_and_a_changed_final(proj):
    build.build(proj)
    out = proj / "out"
    m = manifest.load(out)
    manifest.save(out, {**m, "build_id": "0" * 32})          # a later build started and never published
    with pytest.raises(EngineError, match="stale"):
        deliver.check_build(out / "final.mp4")
    manifest.save(out, m)
    with open(out / "final.mp4", "ab") as f:
        f.write(b"x")
    with pytest.raises(EngineError, match="differs"):
        deliver.check_build(out / "final.mp4")


# ------------------------------------------------------------------------------------------------------------ budget
def test_good_budget_of_this_machine_class(monkeypatch):
    monkeypatch.delenv("VIDEO_ENGINE_HF_WORKERS", raising=False)
    b = resources.plan(resources.auto_workers(8, 15.6), "auto", 8, 15.6)
    assert (b.workers, b.native_workers, b.x264_threads, b.hf_workers, b.mux_threads) == (8, 2, 2, 2, 6)
    assert b.native_workers * b.x264_threads + b.hf_workers * 2 <= b.workers
    small = resources.plan(resources.auto_workers(16, 4.0), "auto", 16, 4.0)
    assert small.workers == 2 and small.mux_threads == 1          # RAM caps the budget


def test_good_budget_env_and_profile(project, tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_ENGINE_HF_WORKERS", raising=False)
    d = tmp_path / "pp"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text("[resources]\nworkers = 4\n")
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    _, tl = project(lambda doc: doc.update(profile="chan"))
    b = resources.budget(tl)
    assert (b.source, b.workers, b.native_workers, b.hf_workers, b.mux_threads) == ("profile", 4, 1, 1, 2)
    assert profiles.load("chan").blake3 == profiles.load().blake3   # a machine setting, not in the hash
    monkeypatch.setenv("VIDEO_ENGINE_WORKERS", "12")
    assert resources.budget(tl).source == "env" and resources.budget(tl).workers == 12


def test_bad_budget_values(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        resources.plan(0)
    d = tmp_path / "pp"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text("[resources]\nworkers = -1\n")
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError):
        profiles.load("chan")


def test_good_manifest_paths_are_portable(project, tmp_path, monkeypatch):
    from video_engine import config
    d, _ = project()
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "cache" / "sound").mkdir(parents=True)
    snd = tmp_path / "cache" / "sound" / "a.wav"
    snd.write_bytes(b"RIFFx")
    assert manifest.portable(d / "assets" / "x.png", d) == "assets/x.png"
    assert manifest.portable(snd, d) == "cache:sound/a.wav"
    assert manifest.portable(config.ENGINE_ROOT / "fonts" / "f.ttf", d) == "engine:fonts/f.ttf"
    assert manifest.resolve("cache:sound/a.wav", d) == tmp_path / "cache" / "sound" / "a.wav"
    assert manifest.resolve("assets/x.png", d) == d / "assets" / "x.png"
    lock = {"sounds": {"music": None, "sfx": {"pop": {**manifest.entry(snd, d), "provenance": {}}}}}
    _, tl = project()
    assert manifest.locked_sounds(lock, tl, ["pop"], d)["sfx"]["pop"][0] == snd


def test_bad_manifest_relative_path_outside_project_stays_absolute(tmp_path):
    other = tmp_path / "elsewhere.wav"
    other.write_bytes(b"x")
    assert manifest.portable(other, tmp_path / "proj") == str(other.resolve())
