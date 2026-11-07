"""Sound sourcing wired into the build: ensure_sounds before mixer.plan (exit 3 with a request), `engine build
--resume` without --ai-qa, credits appended after the plan, gate A02 with the delivery gates, sound cache in
`engine cache --prune`."""
import json
import subprocess
from pathlib import Path

import pytest

from video_engine import build, cache_prune, cli, config, mixer, sfx
from video_engine.sound import cache, request
from video_engine.util import EngineError

BRIEF = {"mood": "tense", "energy": "medium", "bpm_min": 80, "bpm_max": 120, "duration_s": 6.0}


@pytest.fixture(autouse=True)
def own_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")


class _Rep:
    """Stand-in pre-flight report (the geoverum style gates are tested elsewhere); ok = no fail."""
    ok, timings, subtimings, fixes, ai, caption_y_overrides, findings = True, {}, {}, [], None, [], []

    def summary(self):
        return "preflight: OK (stub)"


@pytest.fixture
def no_preflight(monkeypatch):
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())


def tone(path: Path, seconds: float, f: int = 660) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d={seconds}:r=48000",
                    "-af", "volume=-12dB", str(path)], check=True)
    return path


def geo(brief=True):
    def mut(d):
        d["profile"] = "geoverum"
        d["audio_chain"] = {"voiceover_path": "assets/tone.wav", "bgm_gain_db": -8.0, "auto_captions": False,
                            **({"music_brief": dict(BRIEF)} if brief else {"bgm_path": "assets/tone.wav"})}
        sc = d["segments"][2]["gsap_scene_config"]
        sc["pins"] = [{"label": "A", "x": 0.4, "y": 0.3, "at_frame": 3}]
    return mut


def answer(bundle: Path) -> dict:
    req = json.loads((bundle / "request.json").read_text())
    items = []
    for k, it in enumerate(req["items"]):
        secs = 0.3 if it["type"] == "sfx" else 7.0
        f = tone(bundle / "files" / f"{it['slot']}.wav", secs, 500 + 100 * k)
        items.append({"slot": it["slot"], "file": f"files/{f.name}", "source": "epidemic", "id": f"es-{k}",
                      "title": f"Title {k}", "artist": "Artist M" if it["type"] == "music" else "",
                      "duration_ms": round(secs * 1000)})
    (bundle / "response.json").write_text(json.dumps({"request_id": req["request_id"], "items": items}))
    return req


def cache_epidemic_sfx(tl, tmp_path):
    for k, kind in enumerate(sfx.kinds_used(tl)):
        cache.put("sfx", kind, tone(tmp_path / f"{kind}.wav", 0.3, 400 + 50 * k),
                  {"source": "epidemic", "id": f"es-{kind}", "title": kind, "artist": "Epidemic Sound",
                   "credit": cache.credit_line("epidemic", f"es-{kind}", kind, "Epidemic Sound")}, 300)


class _Stop(Exception):
    pass


# --------------------------------------------------------------------------------------------------------- good
def test_good_build_exits_3_with_a_sound_request_when_sounds_are_missing(project, no_preflight, capsys):
    p, tl = project(geo())
    assert cli.main(["build", str(p)]) == cli.EXIT_AWAITING
    req = json.loads((request.bundle_dir(p) / "request.json").read_text())
    assert [i["slot"] for i in req["items"]] == [f"music-{cache.brief_key(BRIEF)}"]  # without require_source, SFX uses local tone
    out = capsys.readouterr().out
    assert "out/sound_request/response.json" in out and f"resume: engine build {p} --resume" in out
    assert not (p / "out" / "final.mp4").exists()


def test_good_build_resume_without_ai_qa_imports_and_appends_credits(project, no_preflight, monkeypatch):
    p, tl = project(geo())
    assert cli.main(["build", str(p)]) == 3
    answer(request.bundle_dir(p))
    seen = {}

    def stop(tl_, pd, wd, use_cache=True, **k):          # first stage after the mix plan: stop the render here
        seen["mix"] = mixer.plan(tl_, pd, [])
        raise _Stop
    monkeypatch.setattr(build, "render_segments", stop)
    with pytest.raises(_Stop):
        cli.main(["build", str(p), "--resume"])         # no --ai-qa: the flag reaches ensure_sounds
    assert not request.bundle_dir(p).exists()
    assert seen["mix"].music.provenance["source"] == "epidemic"                  # musik dari request
    assert {r.provenance["source"] for r in seen["mix"].sounds.values()} == {"local-test"}  # SFX pakai local tone
    credits = (p / "credits.txt").read_text()
    assert credits.count(cache.CREDITS_HEADER) == 1
    assert f"- {seen['mix'].music.provenance['credit']}" in credits
    assert '"Title' in credits and "Artist M" in credits


def test_good_cli_passes_resume_and_resume_command_to_the_build(project, monkeypatch):
    p, _ = project()
    got = {}

    def fake(pd, **k):
        got.update(k)
        raise EngineError("stop")
    monkeypatch.setattr(build, "build", fake)
    assert cli.main(["build", str(p), "--label", "x", "--resume"]) == 1
    assert got["resume"] is True and got["ai_qa"] is None
    assert got["resume_cmd"] == f"engine build {p} --label x --resume"


def test_good_cache_prune_includes_the_sound_cache(project, tmp_path):
    p, _ = project()
    a = cache.put("sfx", "pop", tone(tmp_path / "a.wav", 0.2), {"source": "epidemic", "id": "a", "title": "t",
                  "artist": "x", "credit": cache.credit_line("epidemic", "a", "t", "x")}, 200)
    b = cache.put("sfx", "tick", tone(tmp_path / "b.wav", 0.1, 990), {"source": "local-test", "id": "b", "title": "t",
                  "artist": "", "credit": "test"}, 100)
    b.path.unlink()
    dry = cache_prune.prune([p], config.CACHE_DIR, dry_run=True)
    assert "sound/index/sfx-tick.json" in dry["would_delete"] and dry["bytes"] > 0
    r = cache_prune.prune([p], config.CACHE_DIR)
    assert sorted(x for x in r["deleted"] if x.startswith("sound/")) == \
        sorted(["sound/index/sfx-tick.json", f"sound/files/{b.path.stem}.json"])
    assert cache.lookup("sfx", "pop", "epidemic") == a


def test_good_default_profile_build_makes_no_request_and_no_A02(project):
    p, tl = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    before = (p / "credits.txt").read_text()
    r = build.build(p)
    assert r.frames == tl.total_frames and not request.bundle_dir(p).exists()
    assert not any("A02" in q for q in r.qa) and (p / "credits.txt").read_text() == before
    rep = json.loads((p / "out" / "build_report.json").read_text())
    assert not any("A02" in q for q in rep["qa"]) and "sound" not in rep


# ---------------------------------------------------------------------------------------------------------- bad
def test_bad_build_resume_without_a_response_is_an_error(project, no_preflight, capsys):
    p, _ = project(geo())
    assert cli.main(["build", str(p)]) == 3
    assert cli.main(["build", str(p), "--resume"]) == 1
    assert "response.json" in capsys.readouterr().err


def test_good_local_test_music_does_not_block_a_geoverum_build(project, no_preflight, tmp_path):
    """Geoverum requires no audio source by default (require_source is None): local-test music from bgm_path does not
    block the build — A02 skips without emitting a failure."""
    p, tl = project(geo(brief=False))                  # music from bgm_path: provenance local-test
    cache_epidemic_sfx(tl, tmp_path)
    r = build.build(p)
    assert r.ok and r.final is not None and (p / "out" / "final.mp4").is_file()
    rep = json.loads((p / "out" / "build_report.json").read_text())
    assert rep["ok"] is True and rep["final"] is not None
    assert not any(q.startswith("[FAIL]") and "A02" in q for q in rep["qa"])      # A02 does not fail
    assert any(q.startswith("[PASS] A01") for q in rep["qa"])
