"""Director stage of `engine draft` (storyboards, scoring, taste store, frame review + revisions) with mocked
providers; no network, no rendering (the frame sampler is replaced)."""
from __future__ import annotations

import json
import re

import pytest

from video_engine import director, draft, timeline
from video_engine.ai import agent
from video_engine.cli import main
from video_engine.util import EngineError

from test_draft import client, good_plan, proj, run, storyboards  # noqa: F401  (fixture)

def _jpg() -> bytes:
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (36, 64), (20, 90, 120)).save(buf, "JPEG")
    return buf.getvalue()


JPG = _jpg()


def fake_sampler(tl, pdir, times, workdir):
    return [(t, seg, JPG) for t, seg in times]


def plan_with_goals():
    p = good_plan()
    p["segments"][0]["goal"] = "where: Surabaya"
    p["segments"][1]["goal"] = "what happened to Mallaby"
    return p


# ------------------------------------------------------------------------------------------------- storyboards
def test_good_storyboard_chosen_and_fed_to_draft(proj):
    c = client({"m38": [plan_with_goals()]})
    r = run(proj, c)
    assert r.ok
    sb = json.loads((proj / "draft" / "storyboards.json").read_text())
    assert len(sb["ranked"]) == 3 and sb["chosen"] == 0 and sb["ranked"][0]["concept"]["direction"] == "grounded"
    assert r.report["storyboard"]["direction"] == "grounded"
    draft_req = c.models.calls[0][1]
    chosen = next(x for x in draft_req if isinstance(x, str) and x.startswith("CHOSEN STORYBOARD"))
    assert '"Surabaya" | goal: where: Surabaya | pattern: establish' in chosen
    tl = timeline.load(r.timeline_path)
    assert [s.goal for s in tl.segments] == ["where: Surabaya", "what happened to Mallaby"]
    assert r.report["segments"][0]["goal"] == "where: Surabaya"
    sb_req = c.models.side_calls["STORYBOARD REQUEST"][0][1]
    text = next(x for x in sb_req if isinstance(x, str) and x.startswith("STORYBOARD REQUEST"))
    assert "[0]Surabaya [1]bergadir" in text and "tracing (render cost 3)" in text and "CHANNEL TASTE" in text


def test_good_scores_availability_and_render_cost(proj):
    inp = draft.collect(proj)
    ranked = {x["index"]: x for x in director.rank(storyboards(), inp, director.load_taste("default"))}
    assert all(x["valid"] for x in ranked.values())
    # tracing needs a map image; beat 0 of concept 2 shows `still` (a photo) -> half availability on that beat
    assert ranked[2]["score"]["availability"] == 0.75 and ranked[0]["score"]["availability"] == 1.0
    # comparison needs 2 distinct assets; concept 1 beat 0 shows one
    assert ranked[1]["score"]["availability"] == 0.75
    assert ranked[2]["score"]["render"] < ranked[0]["score"]["render"]        # tracing costs 3


def test_good_map_asset_satisfies_tracing(proj):
    inp = draft.collect(proj)
    sb = storyboards()
    sb["concepts"][2]["beats"][0]["assets"] = ["map"]
    row = next(x for x in director.rank(sb, inp, director.load_taste("default")) if x["index"] == 2)
    assert row["score"]["availability"] == 1.0


def test_bad_concept_out_of_order_or_invented_fact_is_not_picked(proj):
    inp = draft.collect(proj)
    sb = storyboards()
    sb["concepts"][0]["beats"][1]["from_word"] = 2                            # skips word 1
    sb["concepts"][1]["beats"][0]["on_screen_text"] = "Surabaya 1946"          # 1946 is in no source
    ranked = director.rank(sb, inp, director.load_taste("default"))
    bad = {x["index"]: x for x in ranked if not x["valid"]}
    assert set(bad) == {0, 1}
    assert "expected 1" in bad[0]["errors"][0] and "1946" in bad[1]["errors"][0]
    assert ranked[0]["index"] == 2 and ranked[0]["valid"]


def test_good_facts_from_script_and_credits_are_allowed(proj):
    inp = draft.collect(proj)
    assert director.invented("Brigadir Mallaby", director.sources_text(inp)) == []     # script
    assert director.invented("Dinas Peta", director.sources_text(inp)) == []           # credits.txt
    assert director.invented("Jakarta 1999", director.sources_text(inp)) == ["Jakarta", "1999"]


def test_bad_all_concepts_invalid_repairs_then_drafts_without_storyboard(proj):
    sb = storyboards()
    for c in sb["concepts"]:
        c["beats"][1]["to_word"] = 2                                             # word 3 never covered
    c = client({"m38": [good_plan()]}, sb=[sb])
    r = run(proj, c)
    assert r.ok and len(c.models.side_calls["STORYBOARD REQUEST"]) == 2           # one repair round
    second = c.models.side_calls["STORYBOARD REQUEST"][1][1]
    assert any(isinstance(x, str) and "REJECTED EVERY CONCEPT" in x and "cover every word" in x for x in second)
    assert "drafting without a storyboard" in r.report["storyboard"]["warning"]
    assert not any(isinstance(x, str) and x.startswith("CHOSEN STORYBOARD") for x in c.models.calls[0][1])


def test_good_repaired_storyboard_is_used(proj):
    bad = storyboards()
    for c in bad["concepts"]:
        c["beats"][1]["to_word"] = 2
    c = client({"m38": [good_plan()]}, sb=[bad, storyboards()])
    r = run(proj, c)
    assert r.report["storyboard"]["chosen"] == 0 and "warning" not in r.report["storyboard"]


def test_good_no_director_skips_storyboard(proj):
    c = client({"m38": [good_plan()]})
    r = run(proj, c, director=False)
    assert r.ok and not c.models.side_calls["STORYBOARD REQUEST"] and "storyboard" not in r.report
    assert not (proj / "draft" / "storyboards.json").exists()


# ------------------------------------------------------------------------------------------------------ taste
def test_good_taste_recent_opener_penalised_and_remembered(proj):
    inp = draft.collect(proj)
    t = director.load_taste("default")
    plain = {x["index"]: x["score"]["total"] for x in director.rank(storyboards(), inp, t)}
    director.remember("default", "lurus opener", ["continuous zoom"])
    t2 = director.load_taste("default")
    hit = {x["index"]: x["score"] for x in director.rank(storyboards(), inp, t2)}
    assert hit[0]["taste_penalty"] == pytest.approx(0.15 + 0.03) and hit[0]["total"] < plain[0]
    assert "opener used recently" in hit[0]["taste_notes"]
    r = run(proj, client({"m38": [good_plan()]}))                              # chosen concept is remembered
    rec = director.load_taste("default")["recent"]
    chosen = r.report["storyboard"]["chosen"]
    assert rec["openers"][0] == storyboards()["concepts"][chosen]["opener"]


def test_good_taste_feedback_replaces_same_item(_taste_dir):
    director.record_feedback("geo", "liked", "route tracing", "shows movement")
    d = director.record_feedback("geo", "rejected", "route tracing", "too slow")
    assert d["liked"] == [] and d["rejected"][0]["why"] == "too slow"
    assert json.loads((_taste_dir / "geo.json").read_text())["rejected"][0]["what"] == "route tracing"
    assert "rejected: route tracing (because too slow)" in director.taste_text(d)


@pytest.mark.parametrize("verdict,what,why", [("meh", "x", "y"), ("liked", "", "y"), ("liked", "x", " ")])
def test_bad_taste_feedback_rejected(verdict, what, why):
    with pytest.raises(ValueError):
        director.record_feedback("geo", verdict, what, why)


def test_good_geoverum_seed_from_brand_profile(monkeypatch):
    from video_engine import config
    monkeypatch.setenv("VIDEO_ENGINE_TASTE_DIR", str(config.PROFILES_DIR / "taste"))
    t = director.load_taste("geoverum")
    joined = " ".join(t["rules"])
    for want in ("satellite map already moving", "0.41 hard cuts per 10 s", "Epidemic Sound", "No call to action",
                 "watermark top right"):
        assert want in joined, want
    assert t["liked"] and t["rejected"] and t["recent"] == {"openers": [], "transitions": []}


def test_good_cli_taste(capsys, _taste_dir):
    assert main(["taste", "geo", "--like", "year held alone", "--why", "readable"]) == 0
    assert "liked: year held alone (because readable)" in capsys.readouterr().out
    assert main(["taste", "geo"]) == 0 and "year held alone" in capsys.readouterr().out


def test_bad_cli_taste_needs_why(capsys):
    assert main(["taste", "geo", "--reject", "glow"]) == 1
    assert "--why is required" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ pattern data
def test_good_pattern_library_complete():
    pats = director.patterns()
    assert {"progressive_reveal", "comparison", "tracing", "meaningful_repetition", "emphasis_by_pause"} <= set(pats)
    for k, p in pats.items():
        assert p["purpose"] and p["params"] and 1 <= p["render_cost"] <= 3 and p["needs"]["min_assets"] >= 1, k


def test_good_guidance_has_no_minimum_effect_counts():
    g = (director.HERE / "guidance.md").read_text()
    assert not re.search(r"(at least|minimum( of)?) \d+ (effects?|transitions?)", g, re.I)
    assert "Apache License 2.0" in g and "Apache License" in (director.HERE / "NOTICE").read_text()
    assert director.guidance("STORYBOARD").startswith("PLANNING LADDER")


# ------------------------------------------------------------------------------------------------------ review
def test_good_critical_times(proj):
    tl = timeline.load(run(proj, client({"m38": [good_plan()]})).timeline_path)
    ts = director.critical_times(tl)
    times = [t for t, _ in ts]
    assert times == sorted(times) and times[0] == 0.0 and 1.5 in times and len(ts) <= director.REVIEW_MAX_FRAMES
    assert dict(ts)[0.0] == 0 and dict(ts)[times[-1]] == 1 and times[-1] == round((tl.total_frames - 1) / 30, 2)


def test_good_review_pass_keeps_v0(proj):
    c = client({"m38": [plan_with_goals()]})
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    assert r.ok and r.report["review"][0]["verdict"] == "pass" and len(r.report["review"]) == 1
    assert r.report["versions"] == [str(proj / "draft" / "timeline.v0.json")]
    req = c.models.side_calls["DRAFT REVIEW"][0][1]
    head = next(x for x in req if isinstance(x, str) and x.startswith("DRAFT REVIEW"))
    assert "[0.10s]Surabaya" in head and "goal: where: Surabaya" in head and "FRAME t=0.00s seg 0" in req
    assert json.loads((proj / "draft" / "review.v0.json").read_text())["verdict"] == "pass"


REVISE = {"verdict": "revise", "findings": [
    {"t_s": 0.0, "seg": 0, "severity": "major", "issue": "hook shows a still photo", "evidence": "no motion",
     "fix": "open on the map"},
    {"t_s": 2.0, "seg": 1, "severity": "minor", "issue": "label small", "fix": "bigger"}]}


def test_good_review_revision_saves_versions(proj):
    fixed = good_plan()
    fixed["segments"][0]["intensity"] = 1.6
    c = client({"m38": [good_plan(), fixed]}, review=[REVISE, {"verdict": "pass", "findings": []}])
    r = run(proj, c, review=True, review_rounds=2, frame_sampler=fake_sampler)
    assert r.ok and len(r.report["versions"]) == 2
    revise_req = c.models.calls[1][1]
    txt = next(x for x in revise_req if isinstance(x, str) and "FRAME REVIEW FOUND" in x)
    assert "t=0.00s seg 0 [major] hook shows a still photo (evidence: no motion) -> fix: open on the map" in txt
    assert "label small" not in txt                                              # minor findings do not drive revisions
    v0, v1 = (timeline.load(p) for p in r.report["versions"])
    assert v0.segments[0].camera_move.intensity == 1.2 and v1.segments[0].camera_move.intensity == 1.6
    assert timeline.load(r.timeline_path).segments[0].camera_move.intensity == 1.6
    assert [x.get("verdict") for x in r.report["review"] if not x.get("revision")] == ["revise", "pass"]


def test_bad_revision_failing_gates_keeps_previous_version(proj):
    broken = good_plan()
    broken["segments"][1]["source_in_s"] = 2.9                                   # M01
    c = client({"m38": [good_plan(), broken]}, review=[REVISE])
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    rev = next(x for x in r.report["review"] if x.get("revision"))
    assert r.ok and any("M01" in e for e in rev["errors"]) and rev["kept"] == "timeline.v0.json"
    assert len(r.report["versions"]) == 1
    assert timeline.load(r.timeline_path).segments[1].source_in_frame == 6        # v0, not the broken revision


def test_good_one_round_stops_after_revision(proj):
    fixed = good_plan()
    fixed["segments"][0]["intensity"] = 1.6
    c = client({"m38": [good_plan(), fixed]}, review=[REVISE])
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    assert len(c.models.side_calls["DRAFT REVIEW"]) == 1 and len(r.report["versions"]) == 2


def test_bad_review_rounds_out_of_range(proj):
    with pytest.raises(EngineError, match="review_rounds"):
        run(proj, client({"m38": [good_plan()]}), review=True, review_rounds=3)


def test_good_needs_revision_only_on_revise_verdict():
    assert director.needs_revision({"verdict": "pass", "findings": REVISE["findings"]}) == []
    assert [f["seg"] for f in director.needs_revision(REVISE)] == [0]


# ------------------------------------------------------------------------------------------------ agent provider
def test_good_agent_storyboard_draft_review_resume(proj, monkeypatch):
    monkeypatch.setattr(director, "sample_frames", fake_sampler)
    args = ["draft", str(proj), "--provider", "agent", "--no-dom", "--review"]
    root = proj / "ai_request" / "draft"
    assert main(args) == agent.EXIT_AWAITING
    assert "STORYBOARD REQUEST" in (root / "storyboard" / "request.md").read_text()
    assert json.loads((root / "storyboard" / "schema.json").read_text())["properties"]["concepts"]["minItems"] == 3
    (root / "storyboard" / "response.json").write_text(json.dumps(storyboards()))
    assert main(args + ["--resume"]) == 3
    assert "CHOSEN STORYBOARD" in (root / "0" / "request.md").read_text()
    assert json.loads((root / "0" / "storyboard.json").read_text())["direction"] == "grounded"
    (root / "0" / "response.json").write_text(json.dumps(good_plan()))
    assert main(args + ["--resume"]) == 3
    md = (root / "review0" / "request.md").read_text()
    assert "DRAFT REVIEW" in md and "FRAME t=0.00s seg 0" in md and (root / "review0" / "images").is_dir()
    (root / "review0" / "response.json").write_text(json.dumps({"verdict": "pass", "findings": []}))
    assert main(args + ["--resume"]) == 0
    rep = json.loads((proj / "draft_report.json").read_text())
    assert rep["ok"] and rep["review"][0]["verdict"] == "pass" and (proj / "draft" / "timeline.v0.json").is_file()


def test_bad_agent_storyboard_invalid_writes_repair_bundle(proj):
    args = ["draft", str(proj), "--provider", "agent", "--no-dom"]
    root = proj / "ai_request" / "draft"
    assert main(args) == 3
    bad = storyboards()
    for c in bad["concepts"]:
        c["beats"][0]["from_word"] = 1
    (root / "storyboard" / "response.json").write_text(json.dumps(bad))
    assert main(args + ["--resume"]) == 3
    assert "expected 0" in (root / "storyboard1" / "storyboard_errors.txt").read_text()


def test_good_sample_frames_renders_native_jpegs(proj, tmp_path):
    tl = timeline.load(run(proj, client({"m38": [good_plan()]})).timeline_path)
    out = director.sample_frames(tl, proj, [(0.0, 0), (2.0, 1)], tmp_path / "frames")
    assert [(t, s) for t, s, _ in out] == [(0.0, 0), (2.0, 1)]
    from io import BytesIO

    from PIL import Image
    for _, _, jpg in out:
        with Image.open(BytesIO(jpg)) as im:
            assert im.format == "JPEG" and im.size[0] == director.REVIEW_PX
