"""Sound sourcing: [audio] require_source, sound cache and resolver, agent request/response handoff, credits,
gate A02 (audio source), Epidemic Sound connector helpers."""
import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_engine import cache as engine_cache, config, mixer, profiles, sfx
from video_engine.ai.base import AwaitingAgent
from video_engine.gates.audio_source import a02_audio_source
from video_engine.sound import __main__ as sound_cli, cache, epidemic, request
from video_engine.util import EngineError

BRIEF = {"mood": "tense", "energy": "medium", "bpm_min": 80, "bpm_max": 120, "duration_s": 6.0}
SFX_RESULT = {"data": {"soundEffects": {"nodes": [
    {"soundEffect": {"id": "5c5c3085-0e0b-4c29-bba3-c8779dc6daa9", "title": "Cartoon, Pop, Small Lid",
                     "audioFile": {"durationInMilliseconds": 439}, "tags": []}},
    {"soundEffect": {"id": "9e530be5-c2a9-4c81-891e-5ea83a16134d", "title": "Cartoon, Pop, Pops x6",
                     "audioFile": {"durationInMilliseconds": 943}, "tags": []}}]}}}
MUSIC_RESULT = {"data": {"recordings": {"nodes": [
    {"recording": {"id": "5d1e8414-6c0a-3e7b-a992-e2fc2bb2ceb1", "title": "Rhythmania 1", "bpm": 130,
                   "audioFile": {"durationInMilliseconds": 118000},
                   "credits": [{"role": "MAIN_ARTIST", "artist": {"name": "August Wilhelmsson"}}]}},
    {"recording": {"id": "74df2488-8d28-3f25-b764-f288e27dc4dc", "title": "Tranquil's Bliss 4", "bpm": 80,
                   "audioFile": {"durationInMilliseconds": 150000},
                   "credits": [{"role": "COMPOSER", "artist": {"name": "August Wilhelmsson"}}]}}]}}}


@pytest.fixture(autouse=True)
def own_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")


def tone(path: Path, seconds: float, f: int = 660) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d={seconds}:r=48000", str(path)],
                   check=True)
    return path


def prov(i="es-1", source="epidemic", title="T", artist="A"):
    return {"source": source, "id": i, "title": title, "artist": artist,
            "credit": cache.credit_line("epidemic", i, title, artist) if source == "epidemic" else "test"}


def geo(extra=None):
    def mut(d):
        d["profile"] = "geoverum"
        d["audio_chain"] = {"voiceover_path": "assets/tone.wav", "bgm_gain_db": -8.0, "music_brief": dict(BRIEF)}
        sc = d["segments"][2]["gsap_scene_config"]
        sc["pins"] = [{"label": "A", "x": 0.4, "y": 0.3, "at_frame": 3}]
        sc["title"] = "SEKUTU"
        sc["title_at_frame"] = 8
        if extra:
            extra(d)
    return mut


def answer(bundle: Path, req: dict | None = None, **over) -> dict:
    """Fake agent: one generated file per requested slot, response.json written."""
    req = req or json.loads((bundle / "request.json").read_text())
    items = []
    for k, it in enumerate(req["items"]):
        secs = 0.3 if it["type"] == "sfx" else 7.0
        f = tone(bundle / "files" / f"{it['slot']}.wav", secs, 500 + 100 * k)
        items.append({"slot": it["slot"], "file": f"files/{f.name}", "source": "epidemic", "id": f"es-{k}",
                      "title": f"Title {k}", "artist": "" if it["type"] == "sfx" else "Artist M",
                      "duration_ms": round(secs * 1000)})
    doc = {"request_id": req["request_id"], "items": items}
    for k, v in over.items():
        v(doc)
    (bundle / "response.json").write_text(json.dumps(doc))
    return doc


# ---------------------------------------------------------------------------------------------- profile key: good
def test_good_geoverum_and_default_require_no_source():
    assert profiles.load("geoverum").audio.require_source is None
    assert profiles.load("default").audio.require_source is None


def test_good_require_source_accepts_epidemic():
    a = profiles.AudioProfile.model_validate({"target_lufs": -14, "true_peak": -1.5, "lra": 11, "tolerance_lu": 0.2,
                                              "require_source": "epidemic"})
    assert a.require_source == "epidemic"


# ----------------------------------------------------------------------------------------------- profile key: bad
@pytest.mark.parametrize("value", ["local-test", "spotify", "", 1])
def test_bad_require_source_unknown_value_is_rejected(value):
    with pytest.raises(ValidationError, match="require_source"):
        profiles.AudioProfile.model_validate({"target_lufs": -14, "true_peak": -1.5, "lra": 11, "tolerance_lu": 0.2,
                                              "require_source": value})


# ---------------------------------------------------------------------------------------------------- cache: good
def test_good_put_then_lookup_hits_and_touches(tmp_path):
    src = tone(tmp_path / "pop.wav", 0.2)
    r = cache.put("sfx", "pop", src, prov(), 200)
    assert r.path.parent == config.CACHE_DIR / "sound" / "files" and r.path.read_bytes() == src.read_bytes()
    old = 1_000_000_000
    os.utime(r.path, (old, old))
    hit = cache.lookup("sfx", "pop", "epidemic")
    assert hit == r and r.path.stat().st_mtime > old
    assert cache.lookup("sfx", "pop", "local-test") is None and cache.lookup("sfx", "tick", "epidemic") is None


def test_good_brief_key_normalises_mood_and_differs_by_bpm():
    assert cache.brief_key(BRIEF) == cache.brief_key({**BRIEF, "mood": "  Tense "})
    assert cache.brief_key(BRIEF) != cache.brief_key({**BRIEF, "bpm_max": 121})


def test_good_resolve_prefers_epidemic_then_falls_back_to_a_local_test_tone(tmp_path):
    geo_p, default_p = profiles.load("geoverum"), profiles.load("default")
    t = cache.resolve("pop", geo_p)
    assert t.provenance["source"] == "local-test"                     # nothing cached: falls back to a local test tone
    cache.put("sfx", "pop", tone(tmp_path / "l.wav", 0.2, 300), {**prov("lt"), "source": "local-test"}, 200)
    assert cache.resolve("pop", default_p).provenance["source"] == "local-test"
    cache.put("sfx", "pop", tone(tmp_path / "e.wav", 0.2, 900), prov("es-pop"), 200)
    assert cache.resolve("pop", geo_p).provenance["id"] == "es-pop"
    assert cache.resolve("pop", default_p).provenance["id"] == "es-pop"     # no requirement: Epidemic first


def test_good_resolve_music_hits_by_brief_and_misses_otherwise(tmp_path):
    p = profiles.load("geoverum")
    assert cache.resolve_music(BRIEF, p) is None
    cache.put("music", cache.brief_key(BRIEF), tone(tmp_path / "m.wav", 1.0), prov("es-m"), 1000)
    assert cache.resolve_music({**BRIEF, "mood": "TENSE"}, p).provenance["id"] == "es-m"
    assert cache.resolve_music({**BRIEF, "energy": "high"}, p) is None


def test_good_lru_prune_of_the_engine_cache_leaves_a_clean_miss(tmp_path):
    cache.put("sfx", "pop", tone(tmp_path / "e.wav", 0.2), prov(), 200)
    engine_cache.prune(max_bytes=0)                                   # LRU eviction scans <cache>/sound too
    assert cache.lookup("sfx", "pop", "epidemic") is None
    assert cache.resolve("pop", profiles.load("geoverum")).provenance["source"] == "local-test"


def test_good_prune_removes_orphans_and_keeps_entries(tmp_path):
    a = cache.put("sfx", "pop", tone(tmp_path / "a.wav", 0.2), prov("a"), 200)
    b = cache.put("sfx", "tick", tone(tmp_path / "b.wav", 0.1, 990), prov("b"), 100)
    b.path.unlink()                                                   # sidecar and index line now orphaned
    (a.path.parent / "x.wav.1.2.tmp").write_bytes(b"x")
    assert cache.prune(dry_run=True)["would_delete"] and b.path.with_suffix(".json").exists()
    rep = cache.prune()
    assert sorted(rep["deleted"]) == sorted(["index/sfx-tick.json", f"files/{b.path.stem}.json", "files/x.wav.1.2.tmp"])
    assert cache.lookup("sfx", "pop", "epidemic") == a and cache.prune()["deleted"] == []


def test_good_credits_appended_once_under_one_header(tmp_path):
    (tmp_path / "credits.txt").write_text("Kredit\n\nVisual\n- map — X — https://x — PD")
    p1, p2 = prov("es-1", title="Pop"), prov("es-2", title="Copper", artist="Farrell Wooten")
    assert cache.append_credits(tmp_path, [p1, p2, p1, {**prov("lt"), "source": "local-test"}]) == [p1["credit"],
                                                                                                    p2["credit"]]
    assert cache.append_credits(tmp_path, [p1, p2]) == []
    text = (tmp_path / "credits.txt").read_text()
    assert text.count(cache.CREDITS_HEADER) == 1 and text.endswith(f"- {p2['credit']}\n")
    assert p2["credit"] == 'es-2 — Farrell Wooten — "Copper" — https://www.epidemicsound.com — Epidemic Sound licence'


# ----------------------------------------------------------------------------------------------------- cache: bad
@pytest.mark.parametrize("bad", [{"source": "epidemic"}, {**prov(), "source": "spotify"}, {**prov(), "id": " "},
                                 {**prov(), "extra": 1}])
def test_bad_provenance_is_rejected(tmp_path, bad):
    with pytest.raises(EngineError, match="provenance"):
        cache.put("sfx", "pop", tone(tmp_path / "a.wav", 0.2), bad, 200)


def test_bad_non_audio_file_and_unknown_kind_are_rejected(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(EngineError, match="audio file"):
        cache.put("sfx", "pop", tmp_path / "a.txt", prov(), 1)
    with pytest.raises(EngineError, match="unknown SFX kind"):
        cache.put("sfx", "boing", tone(tmp_path / "a.wav", 0.2), prov(), 200)


def test_bad_local_test_has_no_publishable_credit():
    with pytest.raises(EngineError, match="no publishable credit"):
        cache.credit_line("local-test", "x", "t", "a")


# ----------------------------------------------------------------------------------------------- end to end: good
def test_good_e2e_request_exit_3_resume_import_music_credits_and_A02_skip(project, capsys):
    p, tl = project(geo())
    kinds = sfx.kinds_used(tl)
    assert kinds and sound_cli.main([str(p)]) == 3
    b = request.bundle_dir(p)
    assert {f.name for f in b.iterdir()} == {"request.json", "request.md", "schema.json", "files"}
    req = json.loads((b / "request.json").read_text())
    assert [it["slot"] for it in req["items"]] == [f"music-{cache.brief_key(BRIEF)}"]  # SFX fall back to local tones
    assert "resume: python -m video_engine.sound" in capsys.readouterr().out
    assert sound_cli.main([str(p), "--resume"]) == 1                  # resume before answering: error, no re-request
    answer(b, req)
    assert sound_cli.main([str(p), "--resume", "--credits"]) == 0
    assert not b.exists()                                             # imported, bundle removed
    prof = profiles.of(tl)
    assert prof.audio.require_source is None
    for k in kinds:
        assert cache.resolve(k, prof).provenance["source"] == "local-test"   # uncached SFX use the local fallback
    assert cache.resolve_music(BRIEF, prof).provenance["artist"] == "Artist M"
    credits = (p / "credits.txt").read_text()
    assert cache.CREDITS_HEADER in credits and '"Title 0"' in credits and "Epidemic Sound licence" in credits
    mix = mixer.plan(tl, p, [])
    assert mix is not None
    assert {r.provenance["source"] for r in mix.sounds.values()} == {"local-test"}
    assert mix.music.provenance["source"] == "epidemic"
    assert [f.level for f in a02_audio_source(tl, mix)] == ["skip"]   # geoverum requires no audio source
    assert sound_cli.main([str(p)]) == 0 and request.ensure_sounds(tl, p) == []     # all cached: no request


def test_good_without_require_source_only_an_uncached_music_brief_is_requested(project, monkeypatch):
    p, tl = project(geo())
    prof = profiles.of(tl)
    audio = prof.audio.model_copy(update={"require_source": None})
    loose = dataclasses.replace(prof, data=prof.data.model_copy(update={"audio": audio}))
    monkeypatch.setattr(profiles, "of", lambda _tl: loose)
    with pytest.raises(AwaitingAgent):
        request.ensure_sounds(tl, p)
    req = json.loads((request.bundle_dir(p) / "request.json").read_text())
    assert req["required_source"] == "epidemic" and [it["type"] for it in req["items"]] == ["music"]


def test_good_default_profile_needs_no_sounds(project):
    p, tl = project()
    assert request.ensure_sounds(tl, p) == [] and not request.bundle_dir(p).exists()


# ------------------------------------------------------------------------------------------------ end to end: good
def test_good_e2e_local_test_sounds_do_not_fail_A02(project):
    p, tl = project(geo(lambda d: d["audio_chain"].update({"music_brief": None, "bgm_path": "assets/tone.wav"})))
    mix = mixer.plan(tl, p, [])                                       # nothing sourced: local tones and bgm_path
    assert mix is not None
    fs = a02_audio_source(tl, mix)
    assert [f.level for f in fs] == ["skip"]                          # geoverum requires no audio source: skips
    fs = a02_audio_source(tl, mix, require="epidemic")                # preferred by default: warns unless required
    assert fs[0].level == "warn" and "local-test" in fs[0].message and "sfx pop" in fs[0].message


@pytest.mark.parametrize("why, edit, match", [
    ("stale request id", lambda d: d.update(request_id="0" * 16), "request_id"),
    ("slot missing", lambda d: d["items"][0].update(slot="sfx-pop"), "each slot once"),
    ("wrong source", lambda d: d["items"][0].update(source="local-test"), "source"),
    ("file outside bundle", lambda d: d["items"][0].update(file="../../assets/tone.wav"), "inside"),
    ("duration lie", lambda d: d["items"][0].update(duration_ms=5000), "lasts"),
    ("empty title", lambda d: d["items"][0].update(title=" "), "non-empty"),
    ("schema", lambda d: d["items"][0].pop("artist"), "schema"),
])
def test_bad_response_is_rejected_and_nothing_imported(project, why, edit, match):
    p, tl = project(geo())
    with pytest.raises(AwaitingAgent):
        request.ensure_sounds(tl, p)
    answer(request.bundle_dir(p), edit=edit)
    with pytest.raises(EngineError, match=match):
        request.ensure_sounds(tl, p, resume=True)
    assert cache.lookup("sfx", "pop", "epidemic") is None


def test_bad_invalid_json_response(project):
    p, tl = project(geo())
    with pytest.raises(AwaitingAgent):
        request.ensure_sounds(tl, p)
    (request.bundle_dir(p) / "response.json").write_text("{nope")
    with pytest.raises(EngineError, match="invalid JSON"):
        request.ensure_sounds(tl, p, resume=True)


# ---------------------------------------------------------------------------------------------------- gate A02
def test_good_A02_skips_without_requirement_and_honours_the_parameter(project):
    p, tl = project(geo(lambda d: d.update(profile="default")))
    assert [f.level for f in a02_audio_source(tl, None)] == ["skip"]
    assert [f.level for f in a02_audio_source(tl, None, require="epidemic")] == ["pass"]   # no audio files


def test_good_A02_warns_project_bgm_without_a_mix(project):
    p, tl = project(lambda d: d.update(audio_chain={"bgm_path": "assets/tone.wav"}))
    fs = a02_audio_source(tl, None, require="epidemic")
    assert fs[0].level == "warn" and "project-file" in fs[0].message   # preferred by default: warns unless required


# ------------------------------------------------------------------------------------------ Epidemic helpers: good
def test_good_search_args_and_candidates():
    assert epidemic.sfx_search_args("pop") == {"query": {"term": "pop"}, "filter": {"duration": {"max": 1500}},
                                               "first": 10}
    m = epidemic.music_search_args(BRIEF)
    assert m["filter"]["bpm"] == {"min": 80, "max": 120} and m["filter"]["duration"]["min"] == 6000
    assert m["filter"]["vocals"] is False
    c = epidemic.sfx_candidates(SFX_RESULT)
    assert epidemic.pick_sfx(c, "pop")["id"] == "5c5c3085-0e0b-4c29-bba3-c8779dc6daa9"
    mc = epidemic.music_candidates(MUSIC_RESULT)
    assert [x["artist"] for x in mc] == ["August Wilhelmsson", "August Wilhelmsson"]
    assert epidemic.pick_music(mc, BRIEF)["title"] == "Tranquil's Bliss 4"      # first one is 130 bpm


def test_good_retry_later_and_url_handling():
    err = {"errors": [{"message": "x", "extensions": {"code": "RETRY_LATER", "retryAfterSeconds": 12}}]}
    assert epidemic.retry_after(err) == 12 and epidemic.retry_after("RETRY_LATER") == epidemic.RETRY_DEFAULT_S
    url = "https://cdn.example/a/b/sound.wav?X-Sig=secret&exp=1"
    res = {"data": {"soundEffectDownload": {"assetUrl": url}}}
    assert epidemic.asset_url(res) == url and epidemic.redact(url) == "https://cdn.example/a/b/sound.wav"
    assert epidemic.file_ext(url) == ".wav"


# ------------------------------------------------------------------------------------------- Epidemic helpers: bad
def test_bad_helpers_reject_unknown_input():
    assert epidemic.retry_after({"errors": [{"message": "boom"}]}) is None
    too_long = [{**c, "duration_ms": 1501} for c in epidemic.sfx_candidates(SFX_RESULT)]
    assert epidemic.pick_sfx(too_long, "pop") is None                                        # pop cap 1500 ms
    with pytest.raises(ValueError):
        epidemic.sfx_search_args("boing")
    with pytest.raises(ValueError):
        epidemic.asset_url({"data": {"x": {}}})
    with pytest.raises(ValueError):
        epidemic.file_ext("https://cdn.example/a.exe?sig=1")


def test_good_concurrent_credits_appends_keep_every_line(tmp_path):
    import threading
    errors = []
    start = threading.Barrier(8)

    def writer(i):
        try:
            start.wait(timeout=10)
            for j in range(10):
                cache.append_credits(tmp_path, [prov(f"es-{i}-{j}", title=f"T{i}-{j}")])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert errors == []
    lines = [ln for ln in (tmp_path / "credits.txt").read_text().splitlines() if ln.startswith("- ")]
    assert len(lines) == 80 and len(set(lines)) == 80
    assert (tmp_path / "credits.txt").read_text().count(cache.CREDITS_HEADER) == 1


def test_bad_real_build_does_not_generate_a_tone(project, monkeypatch):
    """Without VIDEO_ENGINE_LOCAL_TONES, every kind with no licensed file errors, including riser and rumble."""
    monkeypatch.delenv("VIDEO_ENGINE_LOCAL_TONES", raising=False)
    p, tl = project(geo(lambda d: d["audio_chain"].update(sfx=[
        {"kind": "riser", "at_frame": 0}, {"kind": "rumble", "at_frame": 10}])))
    prof = profiles.of(tl)
    slots = {it["slot"] for it in cache.missing(tl, prof)}
    assert {"sfx-riser", "sfx-rumble"} <= slots
    for kind in ("riser", "rumble"):
        with pytest.raises(EngineError, match=f"SFX kind '{kind}'"):
            cache.resolve(kind, prof)
    tone_path = cache.resolve("riser", prof, allow_generated=True)
    assert tone_path.provenance["source"] == "local-test"
    cache.put("sfx", "rumble", tone(p / "rumble.wav", 0.4, 80), prov("es-rumble"), 400)
    assert cache.resolve("rumble", prof).provenance["id"] == "es-rumble"
    assert "sfx-rumble" not in {it["slot"] for it in cache.missing(tl, prof)}
