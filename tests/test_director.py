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
    assert "pattern: establish | assets: still" in chosen and "map_recipe country_reveal" in chosen   # pattern -> recipe
    assert "footage_beat" in chosen and chosen.count("map_recipe") == 1                            # none for footage
    tl = timeline.load(r.timeline_path)
    assert [s.goal for s in tl.segments] == ["where: Surabaya", "what happened to Mallaby"]
    assert r.report["segments"][0]["goal"] == "where: Surabaya"
    sb_req = c.models.side_calls["STORYBOARD REQUEST"][0][1]
    text = next(x for x in sb_req if isinstance(x, str) and x.startswith("STORYBOARD REQUEST"))
    assert "[0]Surabaya [1]bergadir" in text and "tracing (render cost 3)" in text and "CHANNEL TASTE" in text
    assert "| focal: Surabaya harbour | cause: the word Surabaya" in chosen and "| focal: Mallaby's car" in chosen
    assert "focal: the one thing the eye must land on" in text and "hero_moment: the payoff" in text


def test_good_chosen_text_names_payoff_and_what_is_left_out(proj):
    inp = draft.collect(proj)
    c = storyboards(left_out=["a casualty count", "the city map"])["concepts"][0]
    txt = director.chosen_text(inp, c)
    assert "Payoff (hero moment; hold it long enough to read): Mallaby" in txt
    assert "Left out on purpose: a casualty count; the city map" in txt
    assert "Left out" not in director.chosen_text(inp, storyboards()["concepts"][0])


def test_bad_storyboard_span_without_focal_breaks_the_schema(proj):
    from video_engine.ai.base import schema_errors
    inp = draft.collect(proj)
    sb = storyboards()
    assert schema_errors(sb, director.schema(inp)) == []
    del sb["concepts"][1]["spans"][0]["focal"]
    assert schema_errors(sb, director.schema(inp)) == ["$.concepts[1].spans[0]: missing required 'focal'"]


# ------------------------------------------------------------------------------------------------- craft notes
def _ranked(inp, sb):
    return {x["index"]: x for x in director.rank(sb, inp, director.load_taste("default"))}


def test_good_clean_concepts_have_no_craft_notes(proj):
    inp = draft.collect(proj)
    for x in _ranked(inp, storyboards()).values():
        s = x["score"]
        assert x["valid"] and s["craft_notes"] == [] and s["craft_penalty"] == 0
        assert s["total"] == round(sum(director.WEIGHTS[k] * s[k] for k in director.WEIGHTS) - s["taste_penalty"], 3)


def test_bad_generic_span_gets_a_note_and_a_lower_total(proj):
    inp = draft.collect(proj)
    clean = _ranked(inp, storyboards())[0]["score"]["total"]
    sb = storyboards()
    sb["concepts"][0]["spans"][1].update(focal="the main subject", goal="the viewer understands the context")
    row = _ranked(inp, sb)[0]
    assert row["valid"] and row["score"]["craft_notes"] == [
        "span 1: focal, goal and text name nothing from its narration, the script or its assets (could be any video)"]
    assert row["score"]["total"] == pytest.approx(clean - director.CRAFT_PENALTY)


def test_good_specific_span_is_not_generic_in_any_language(proj):
    """Short names, possessives and inflections count; short lower-case words of the sources do not."""
    inp = draft.collect(proj)
    inp.script = "Surabaya ada di Jawa. Brigadir Mallaby tewas di sana."
    sb = storyboards()
    sb["concepts"][0]["spans"][0].update(focal="Jawa", goal="di mana", on_screen_text="")     # a short name
    sb["concepts"][0]["spans"][1].update(focal="Mallaby's", goal="")                          # a possessive
    assert _ranked(inp, sb)[0]["score"]["craft_notes"] == []
    sb["concepts"][0]["spans"][0].update(focal="Ada", goal="the map")       # "ada" is a common word of the sources
    assert _ranked(inp, sb)[0]["score"]["craft_notes"][0].startswith("span 0: focal, goal and text name nothing")


def test_bad_text_said_twice_over_budget_or_with_two_numbers(proj):
    inp = draft.collect(proj)
    inp.script = "Surabaya 1945. Brigadir Mallaby tewas 30 Oktober."
    clean = _ranked(inp, storyboards())[0]["score"]["total"]
    cases = {"Surabaya": "span 1: on-screen text repeats span 0 (say it once)",
             "kota pelabuhan Surabaya di pantai utara pulau yang ramai": "span 1: on-screen text has 9 words (budget 8)",
             "1945, 30 Oktober": "span 1: 2 numbers in one on-screen text (one number gets to be big)"}
    for text, note in cases.items():
        sb = storyboards()
        sb["concepts"][0]["spans"][1]["on_screen_text"] = text
        row = _ranked(inp, sb)[0]
        assert row["valid"] and row["score"]["craft_notes"] == [note], text
        assert row["score"]["total"] == pytest.approx(clean - director.CRAFT_PENALTY), text


def test_bad_identical_pattern_sequences_note_every_concept(proj):
    inp = draft.collect(proj)
    clean = _ranked(inp, storyboards())[0]["score"]["total"]
    sb = storyboards()
    for c in sb["concepts"]:
        c["spans"][0]["pattern"] = "establish"                                   # concept 0 already uses it
    ranked = _ranked(inp, sb)
    assert all(x["score"]["craft_notes"] == ["every concept has the same pattern sequence (they differ only in surface)"]
               for x in ranked.values())
    assert ranked[0]["score"]["total"] == pytest.approx(clean - director.CRAFT_PENALTY)


def test_good_craft_penalty_is_bounded(proj):
    inp = draft.collect(proj)
    v = {**director.validate(storyboards()["concepts"][0], inp, director.patterns()), "notes": ["n"] * 20}
    s = director.score(storyboards()["concepts"][0], v, director.load_taste("default"))
    assert s["craft_penalty"] == director.CRAFT_PENALTY_MAX and len(s["craft_notes"]) == 20


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
    sb["concepts"][2]["spans"][0]["assets"] = ["map"]
    row = next(x for x in director.rank(sb, inp, director.load_taste("default")) if x["index"] == 2)
    assert row["score"]["availability"] == 1.0


def test_bad_concept_out_of_order_or_invented_fact_is_not_picked(proj):
    inp = draft.collect(proj)
    sb = storyboards()
    sb["concepts"][0]["spans"][1]["from_word"] = 2                            # skips word 1
    sb["concepts"][1]["spans"][0]["on_screen_text"] = "Surabaya 1946"          # 1946 is in no source
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
        c["spans"][1]["to_word"] = 2                                             # word 3 never covered
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
        c["spans"][1]["to_word"] = 2
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


def test_bad_corrupt_taste_store_names_the_file(_taste_dir):
    _taste_dir.mkdir(parents=True, exist_ok=True)
    (_taste_dir / "geo.json").write_text('{"channel": "geo", "liked": [')          # a half-written file
    with pytest.raises(EngineError, match=r"taste store .*geo\.json: invalid JSON"):
        director.load_taste("geo")
    (_taste_dir / "geo.json").write_text("[]")
    with pytest.raises(EngineError, match="expected a JSON object"):
        director.load_taste("geo")


def test_good_taste_save_is_atomic_and_leaves_no_part_file(_taste_dir):
    director.record_feedback("geo", "liked", "route tracing", "shows movement")
    director.remember("geo", "opener A", ["hold"])
    assert sorted(f.name for f in _taste_dir.iterdir()) == ["geo.json"]
    assert json.loads((_taste_dir / "geo.json").read_text())["recent"]["openers"] == ["opener A"]


def test_good_storyboard_prompt_names_the_frame_format(proj):
    inp = draft.collect(proj)
    assert "director of a vertical documentary short." in director.prompt(inp, director.load_taste("default"))
    inp.settings = inp.settings.model_copy(update={"width": 1920, "height": 1080})
    assert "director of a landscape 16:9 documentary video." in director.prompt(inp, director.load_taste("default"))


FORMAT_WORDS = re.compile(r"vertical|landscape|portrait|9:16|16:9|1080x1920|1920x1080", re.I)


def test_bad_no_literal_format_words_in_prompts(proj):
    """Both prompts name the format through director.format_words only: the guidance carries none, and a landscape
    request never says vertical (nor a portrait one landscape)."""
    for section in ("STORYBOARD", "EDIT", "REVIEW"):
        assert not FORMAT_WORDS.search(director.guidance(section)), section
    assert not hasattr(draft, "_format_words")                                    # one implementation
    inp = draft.collect(proj)
    taste = director.load_taste("default")
    for w, h, own, other in ((1080, 1920, "vertical", "landscape"), (1920, 1080, "landscape", "vertical")):
        inp.settings = inp.settings.model_copy(update={"width": w, "height": h})
        assert director.format_words(inp.settings).startswith(own)
        # the draft prompt's shot table (framing.describe) names both layouts on purpose: its header line is checked
        for text in (director.prompt(inp, taste), draft.prompt(inp).splitlines()[0]):
            assert own in text and other not in text.lower()


def test_good_draft_prompt_carries_the_edit_guidance_once(proj):
    p = draft.prompt(draft.collect(proj))
    edit = director.guidance("EDIT")
    assert edit.startswith("CRAFT.") and p.count(edit) == 1 and p.endswith("Return JSON only, matching the schema.")
    assert p.index("RULES") < p.index(edit)


def test_bad_edit_guidance_text_is_not_copied_into_code():
    """The principles have one home (guidance.md): no line of a section is written into draft.py or director."""
    from pathlib import Path
    code = Path(draft.__file__).read_text() + (director.HERE / "__init__.py").read_text()
    for section in ("STORYBOARD", "EDIT", "REVIEW"):
        for line in director.guidance(section).splitlines():
            if len(line) > 40:
                assert line.strip() not in code, line


def test_good_geoverum_seed_from_brand_profile(monkeypatch):
    from video_engine import config
    monkeypatch.setenv("VIDEO_ENGINE_TASTE_DIR", str(config.PROFILES_DIR / "taste"))
    t = director.load_taste("geoverum")
    blob = json.dumps(t)
    # Preferences live on liked/rejected. The shipped store has no parallel rules list and no hard-cut quota.
    assert "already-moving satellite map" in blob
    assert any("call to action" in x["what"] for x in t["rejected"])
    assert "hard cuts per 10 s" not in blob
    assert t.get("rules", []) == []
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
    assert {0.27, 1.87} <= set(times)                       # under the cap every segment start is sampled


def test_good_critical_times_sample_a_few_frames_into_an_entrance(proj):
    plan = good_plan()
    plan["segments"][0]["overlays"][0]["start_s"] = 0.5                         # the label enters at frame 15
    tl = timeline.load(run(proj, client({"m38": [plan]})).timeline_path)
    ts = director.critical_times(tl)
    assert (round((15 + director.ENTRANCE_FRAMES) / 30, 2), 0) in ts and ts == director.critical_times(tl)


def test_bad_too_many_moments_keep_hook_last_frame_and_entrances_before_edges(proj):
    from video_engine.timeline import Overlay
    tl = timeline.load(run(proj, client({"m38": [good_plan()]})).timeline_path)
    seg = tl.segments[1]
    seg.overlays = [Overlay(id=f"o{k}", box=[100, 100, 300, 80], kind="label", text="x", start_frame=k) for k in range(30)]
    ts = director.critical_times(tl)
    times = set(t for t, _ in ts)
    must = {0.0, 1.5, round((tl.total_frames - 1) / 30, 2)}
    entrances = {round((seg.start_frame + min(k + director.ENTRANCE_FRAMES, seg.n_frames - 1)) / 30, 2) for k in range(30)}
    assert len(ts) == director.REVIEW_MAX_FRAMES and must <= times
    assert times - must <= entrances | {round(director.ENTRANCE_FRAMES / 30, 2)}   # segment edges give way
    assert ts == director.critical_times(tl) and [t for t, _ in ts] == sorted(times)


def test_good_review_pass_keeps_v0(proj):
    c = client({"m38": [plan_with_goals()]})
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    assert r.ok and r.report["review"][0]["verdict"] == "pass" and len(r.report["review"]) == 1
    assert r.report["versions"] == [str(proj / "draft" / "timeline.v0.json")]
    req = c.models.side_calls["DRAFT REVIEW"][0][1]
    head = next(x for x in req if isinstance(x, str) and x.startswith("DRAFT REVIEW"))
    assert "[0.10s]Surabaya" in head and "goal: where: Surabaya" in head and "FRAME t=0.00s seg 0" in req
    assert "SEGMENTS (the brief)" in head and "asset still" not in head and "kota disebut" not in head   # fresh eye
    assert json.loads((proj / "draft" / "review.v0.json").read_text())["verdict"] == "pass"


REVISE = {"verdict": "revise", "not_checked": [], "findings": [
    {"t_s": 0.0, "seg": 0, "category": "motion", "severity": "major", "issue": "hook shows a still photo",
     "evidence": "no motion", "fix": "open on the map"},
    {"t_s": 1.87, "seg": 1, "category": "legibility", "severity": "minor", "issue": "label small",
     "evidence": "label 20 px tall", "fix": "bigger"}]}


def test_good_review_revision_saves_versions(proj):
    fixed = good_plan()
    fixed["segments"][0]["intensity"] = 1.6
    c = client({"m38": [good_plan(), fixed]}, review=[REVISE, {"verdict": "pass", "findings": [], "not_checked": []}])
    r = run(proj, c, review=True, review_rounds=2, frame_sampler=fake_sampler)
    assert r.ok and len(r.report["versions"]) == 2
    revise_req = c.models.calls[1][1]
    txt = next(x for x in revise_req if isinstance(x, str) and "FRAME REVIEW FOUND" in x)
    assert "- motion: t=0.00s seg 0 [major] hook shows a still photo (evidence: no motion) -> fix: open on the map" in txt
    assert "largest fault first: framing, hierarchy, motion, legibility, grounding" in txt and "Fix the largest fault first" in txt
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


def test_good_malformed_review_keeps_the_gate_passing_draft(proj):
    """A review answer that does not match its schema is recorded and never read field by field: the draft that
    passed the gates is still written."""
    c = client({"m38": [plan_with_goals()]}, review=[{"verdict": "revise", "findings": [{"seg": "zero"}]}])
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    assert r.ok and r.timeline_path == proj / "master_timeline.draft.json" and r.timeline_path.is_file()
    entry = r.report["review"][0]
    assert entry["verdict"] is None and entry["kept"] == "timeline.v0.json"
    assert any(e.startswith("schema: $.findings[0]") for e in entry["errors"])
    assert r.report["versions"] == [str(proj / "draft" / "timeline.v0.json")]


def test_good_unavailable_reviewer_keeps_the_gate_passing_draft(proj, monkeypatch):
    c = client({"m38": [plan_with_goals()]})
    real = c.models.generate_content

    def flaky(model, contents, config):
        if any(isinstance(x, str) and x.startswith("DRAFT REVIEW") for x in contents):
            raise EngineError("reviewer is down")
        return real(model, contents, config)
    monkeypatch.setattr(c.models, "generate_content", flaky, raising=False)
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    assert r.ok and r.timeline_path.is_file()
    assert r.report["review"][0]["errors"] == ["review unavailable: reviewer is down"]


def test_good_review_finding_about_an_unknown_segment_is_dropped(proj):
    ghost = {"verdict": "revise", "not_checked": [], "findings": [
        {"t_s": 1.0, "seg": 99, "category": "grounding", "severity": "blocker", "issue": "x", "evidence": "e", "fix": "y"}]}
    c = client({"m38": [plan_with_goals()]}, review=[ghost])
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    assert r.ok and r.report["review"][0]["findings"] == [] and len(c.models.calls) == 1     # no revision request
    assert r.report["review"][0]["dropped"][0]["dropped"] == "no such segment"


def test_bad_review_finding_without_a_sent_frame_is_dropped(proj):
    unseen = {"verdict": "revise", "not_checked": ["motion between the sampled frames"], "findings": [
        {"t_s": 2.5, "seg": 1, "category": "framing", "severity": "major", "issue": "map too small",
         "evidence": "predicted, not seen", "fix": "zoom in"}]}
    c = client({"m38": [plan_with_goals()]}, review=[unseen])
    r = run(proj, c, review=True, frame_sampler=fake_sampler)
    entry = r.report["review"][0]
    assert r.ok and entry["findings"] == [] and len(c.models.calls) == 1                     # no revision request
    assert entry["dropped"] == [{**unseen["findings"][0], "dropped": "no frame was sent at t_s"}]
    assert entry["not_checked"] == ["motion between the sampled frames"]
    assert json.loads((proj / "draft" / "review.v0.json").read_text())["dropped"][0]["t_s"] == 2.5


def test_good_finding_near_a_sent_frame_is_kept():
    near = {"t_s": 1.9, "seg": 1}
    kept, dropped = director.cited([near, {"t_s": 1.6, "seg": 1}, {"t_s": 1.87, "seg": 7}], {0, 1}, [0.0, 1.87])
    assert kept == [near] and [d["dropped"] for d in dropped] == ["no frame was sent at t_s", "no such segment"]


def _f(cat, sev, issue):
    return {"t_s": 0.0, "seg": 0, "category": cat, "severity": sev, "issue": issue, "evidence": "e", "fix": "f"}


def test_good_findings_text_lists_the_largest_fault_first():
    fs = [_f("grounding", "blocker", "g"), _f("motion", "major", "m-major"), _f("legibility", "major", "l"),
          _f("motion", "blocker", "m-blocker"), _f("framing", "major", "fr"), _f("hierarchy", "minor", "h")]
    issues = [re.search(r"\] (\S+) \(", line).group(1) for line in director.findings_text(fs).splitlines()]
    assert issues == ["fr", "h", "m-blocker", "m-major", "l", "g"]


def test_bad_grounding_blocker_always_asks_for_a_revision():
    g, fr = _f("grounding", "blocker", "unsupported year on screen"), _f("framing", "major", "map too small")
    assert director.needs_revision({"verdict": "pass", "findings": [fr, g]}) == [g]          # whatever the verdict
    assert director.needs_revision({"verdict": "revise", "findings": [fr, g]}) == [fr, g]


def test_bad_storyboard_that_breaks_its_schema_is_repaired_not_ranked(proj):
    """Concepts whose fields are not what the schema promises never reach rank(): the errors go back to the model."""
    broken = {"concepts": [{"name": "x", "direction": "grounded", "opener": "o", "spans": "all of it",
                            "self_scores": {"narration_fit": "8/10", "clarity": 7}}] * 3}
    c = client({"m38": [good_plan()]}, sb=[broken, storyboards()])
    r = run(proj, c)
    assert r.ok and r.report["storyboard"]["chosen"] is not None
    repair = c.models.side_calls["STORYBOARD REQUEST"][1][1]
    txt = next(x for x in repair if isinstance(x, str) and "REJECTED EVERY CONCEPT" in x)
    assert "schema: $.concepts[0].spans: expected array" in txt and "narration_fit: expected number" in txt


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
    assert not (root / "review0" / "timeline.json").exists()          # fresh eye: frames and a brief, not the build
    (root / "review0" / "response.json").write_text(json.dumps({"verdict": "pass", "findings": [], "not_checked": []}))
    assert main(args + ["--resume"]) == 0
    rep = json.loads((proj / "draft_report.json").read_text())
    assert rep["ok"] and rep["review"][0]["verdict"] == "pass" and (proj / "draft" / "timeline.v0.json").is_file()


def test_bad_agent_storyboard_invalid_writes_repair_bundle(proj):
    args = ["draft", str(proj), "--provider", "agent", "--no-dom"]
    root = proj / "ai_request" / "draft"
    assert main(args) == 3
    bad = storyboards()
    for c in bad["concepts"]:
        c["spans"][0]["from_word"] = 1
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
            assert im.format == "JPEG" and im.size[0] == director.REVIEW_PX and im.size[1] % 2 == 0


def test_good_image_jpeg_downscales_in_process(tmp_path, monkeypatch):
    import subprocess
    from io import BytesIO

    from PIL import Image

    from video_engine import util
    png, odd = tmp_path / "f.png", tmp_path / "odd.png"
    Image.new("RGB", (1080, 1920), (10, 20, 30)).save(png)
    Image.new("RGBA", (1000, 1001), (10, 20, 30, 255)).save(odd)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("a downscale spawns no process"))
    for src, size in ((png, (720, 1280)), (odd, (720, 720))):                   # REVIEW_PX wide, even height
        with Image.open(BytesIO(util.image_jpeg(src, director.REVIEW_PX))) as im:
            assert im.format == "JPEG" and im.size == size
    with Image.open(BytesIO(util.image_jpeg(png, 384, quality=80, box=True))) as im:   # draft thumbnails
        assert im.size == (216, 384)


def test_bad_image_jpeg_of_a_non_image_raises(tmp_path):
    from PIL import UnidentifiedImageError

    from video_engine import util
    bad = tmp_path / "x.png"
    bad.write_text("not an image")
    with pytest.raises(UnidentifiedImageError):
        util.image_jpeg(bad, director.REVIEW_PX)
