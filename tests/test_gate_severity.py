"""Gate severity split (video_engine/gates/severity.py): technical fails block, style and advisory gates warn unless the
channel requires them, hold:"read" intent and project waivers lower only non-required style/advisory findings."""
import pytest

from video_engine import config, gate_registry as reg, gate_tools, layout, profiles
from video_engine.gates import severity, style
from video_engine.gates.core import Finding, Report
from video_engine.util import EngineError

from test_layout import beats_doc, no_print
from test_style_gates import layout as lay, pins_every


def chan(tmp_path, monkeypatch, body: str):
    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "geoverum.toml").write_text(profiles.flatten("geoverum"))
    (d / "chan.toml").write_text(body)
    monkeypatch.setattr(config, "PROFILES_DIR", d)


CHAN = "[gates.camera]\nhold_warn_s = 1.7\nhold_fail_s = 3.0\n[gates.novelty]\nmax_gap_s = 3.0\n[gates.cuts]\n" \
       "warn_per_10s = 0.41\nfail_per_10s = 1.65\nfirst_warn_s = 21.0\nfirst_fail_s = 6.5\n"


def two_maps(profile="geoverum", **extra):
    def mut(d):
        lay(("map", 30, pins_every(2, 30)), ("photo", 30, None), profile=profile)(d)
        for k, v in extra.items():
            if k == "seg1":
                d["segments"][1].update(v)
            else:
                d[k] = v
    return mut


def hold(seg=1, lv="fail", a=930, b=1050):
    return Finding("P01", lv, f"camera holds (frames {a}-{b})", seg, frames=(a, b))


def test_good_style_fail_not_required_becomes_warn_and_does_not_block(project):
    _, tl = project(two_maps())
    rep = Report(severity.apply([Finding("N01", "fail", "nothing new for 4 s")], tl))
    assert [f.level for f in rep.findings] == ["warn"] and rep.ok
    assert rep.findings[0].waived.startswith("fail: lint gate not required")


def test_bad_required_style_fail_still_blocks(project, strict_geoverum):
    _, tl = project(two_maps(gates={"require": ["P01"]}))
    rep = Report(severity.apply([hold()], tl))
    assert severity.required(tl) >= {"S01", "P01"}
    assert [f.level for f in rep.findings] == ["fail"] and not rep.ok


def test_bad_project_cannot_waive_a_channel_required_rule(project, strict_geoverum):
    _, tl = project(two_maps(gates={"waive": ["S01", "R14"]}))
    fs = severity.apply([Finding("S01", "fail", "silence gap"), Finding("R14", "fail", "licence not in allowlist")], tl)
    assert [f.level for f in fs if f.gate in ("S01", "R14")] == ["fail", "info"]  # required S01 stays fail; non-required lint R14 waives to info
    assert not any(f.gate == "GW01" for f in fs)  # Unapplicable waivers are ignored without emitting a separate meta-gate finding


def test_bad_project_hold_intent_cannot_lower_a_required_rule(project):
    _, tl = project(two_maps(seg1={"hold": "read"}, gates={"require": ["P01"]}))
    fs = severity.apply([hold(lv="warn", a=930, b=990)], tl)
    assert [f.level for f in fs] == ["warn"] and fs[0].waived is None


def test_good_hold_read_turns_style_warn_into_info_for_that_segment_only(project, tmp_path, monkeypatch):
    chan(tmp_path, monkeypatch, CHAN)
    _, tl = project(two_maps("chan", seg1={"hold": "read"}))
    inside, outside = hold(lv="warn", a=930, b=990), hold(seg=0, lv="warn", a=100, b=160)
    fs = severity.apply([inside, outside], tl)
    assert [f.level for f in fs] == ["info", "warn"] and "hold" in fs[0].waived


def test_bad_hold_read_does_not_touch_other_gates(project, tmp_path, monkeypatch):
    chan(tmp_path, monkeypatch, CHAN)
    _, tl = project(two_maps("chan", seg1={"hold": "read"}))
    fs = severity.apply([Finding("C01", "fail", "x", 1, frames=(930, 931)), Finding("R38", "fail", "black", 1)], tl)
    assert [f.level for f in fs] == ["warn", "fail"]


def test_good_project_waive_and_require_on_a_channel_without_requirements(project, tmp_path, monkeypatch):
    chan(tmp_path, monkeypatch, CHAN)
    _, tl = project(two_maps("chan", gates={"waive": ["N01"], "require": ["C01"]}))
    fs = severity.apply([Finding("N01", "warn", "gap"), Finding("C01", "fail", "rate")], tl)
    assert [f.level for f in fs] == ["info", "fail"]


def test_bad_unknown_required_gate_in_profile_and_project(project, tmp_path, monkeypatch):
    chan(tmp_path, monkeypatch, '[gates]\nrequire = ["T02"]\n')
    with pytest.raises(EngineError, match="unknown style gate"):
        profiles.load("chan")
    with pytest.raises(EngineError, match="unknown style gate"):
        project(two_maps(gates={"require": ["X99"]}))


def test_bad_never_advisory_gate_rejected_in_profile(tmp_path, monkeypatch):
    assert profiles.NEVER_ADVISORY == reg.NEVER_ADVISORY
    for gid in ("T02", "R07", "R14", "A02", "R56px"):
        chan(tmp_path, monkeypatch, f'[gates]\nadvisory = ["{gid}"]\n')
        with pytest.raises(EngineError, match="gates.advisory"):
            profiles.load("chan")


def test_good_warn_only_lint_gates_block_when_required(project):
    _, tl = project(two_maps(gates={"require": ["A02", "N01", "K01", "D01", "P03", "U01"]}))
    fs = severity.apply([Finding(g, "warn", "x") for g in ("A02", "N01", "K01", "D01", "P03", "U01")], tl)
    assert {f.level for f in fs} == {"fail"}


def test_good_geoverum_requires_no_style_gate_and_lowers_advisory_gates(project):
    p = profiles.load("geoverum")
    assert p.gates.require == []                         # no style gate blocks by default on Geoverum
    assert p.gates.advisory == []   # Advisory list is empty because lint gates automatically warn unless required.
    assert all(g in reg.LINT_GATE_IDS for g in ("R64", "E01", "R56", "T01", "R44", "L01", "L06", "R62", "R63", "Z01", "O01", "M02", "B01"))
    assert not set(p.gates.advisory) & set(reg.NEVER_ADVISORY)
    assert p.gates.camera.min_drift_per_s == 0.06
    _, tl = project(two_maps())
    assert [f.level for f in style.p02_drift(tl, p.gates.camera)] == ["pass"]


def test_good_geoverum_style_and_advisory_fails_become_warn_while_safety_gates_block(project):
    """Style gates (S01 too) and advisory gates lower to warn on Geoverum; kernel gates still fail."""
    _, tl = project(two_maps())
    fs = severity.apply([Finding("C01", "fail", "3 shot changes per 10 s"),
                         Finding("C02", "fail", "CTA on screen"),
                         Finding("D01", "fail", "duration 30.0 s outside 45-90 s"),
                         Finding("K01", "fail", "no ending keyword"),
                         Finding("K02", "fail", "keyword gone on the final frame"),
                         Finding("V01", "fail", "hard cut between two maps"),
                         Finding("R64", "fail", "Lc below target"),
                         Finding("L02", "fail", "title tracking"),
                         Finding("R63", "fail", "caption hits a pin"),
                         Finding("S01", "fail", "silent gap 1.00 s at 1.00 s"),
                         Finding("T02", "fail", "segments overlap"),
                         Finding("R14", "fail", "licence not in allowlist")], tl)
    assert [f.level for f in fs] == ["warn"] * 10 + ["fail", "warn"] and not Report(fs).ok  # R14 is a lint gate (warn), while T02 is a kernel gate (fail)


def test_bad_p02_drift_below_floor_fails(project, monkeypatch):
    _, tl = project(two_maps())
    real = profiles.of

    def slow(t):
        pr = real(t)
        th = pr.data.theme.model_copy(update={"camera_drift_per_s": 0.03})
        return profiles.Profile(pr.name, pr.data.model_copy(update={"theme": th}), pr.blake3 + "-slow")
    monkeypatch.setattr(profiles, "of", slow)
    fs = style.p02_drift(tl, real(tl).gates.camera)
    assert [f.level for f in fs] == ["fail"] and "waits are still" in fs[0].message


def test_good_default_profile_hash_unchanged_by_empty_require():
    assert not profiles.load().gates.require
    assert not profiles.load().gates.advisory
    assert profiles.load().blake3 == "1048453ef3a189d0b65c981fe187575821cf5520bc26298dd48282ff72e61181"


def test_good_summary_lists_fails_warns_and_waived_separately(project, tmp_path, monkeypatch):
    chan(tmp_path, monkeypatch, CHAN)
    _, tl = project(two_maps("chan", gates={"waive": ["N01"]}))
    # T02 is a kernel gate (remains fail), whereas style/lint gates lower to warn.
    fs = severity.apply([Finding("T02", "fail", "segments overlap"), Finding("C01", "fail", "rate"),
                         Finding("N01", "warn", "gap"), Finding("T02", "pass", "ok")], tl)
    s = severity.summary(fs, tl)
    assert [r["gate"] for r in s["fails"]] == ["T02"] and s["fails"][0]["class"] == "technical"
    assert [r["gate"] for r in s["warns"]] == ["C01"]
    assert {r["gate"] for r in s["waived"]} == {"C01", "N01"}


def test_good_beat_hold_read_turns_off_pulses_on_its_wait(project, monkeypatch):
    no_print(monkeypatch)

    def mut(doc):
        beats_doc(doc)
        doc["segments"][2]["gsap_scene_config"]["beats"][2]["hold"] = "read"
    d, tl = project(mut)
    seg = tl.segments[2]
    layout.apply_segment(seg, tl, d)
    last = seg.gsap_scene_config.camera_path[-1]
    assert last.pulse in (0, 0.0, [0.0]) or (isinstance(last.pulse, list) and not any(last.pulse))
    last = seg.gsap_scene_config.beats[-1]
    assert severity.hold_ranges(tl) == [(seg.start_frame + last.at_frame, seg.end_frame)]


def test_bad_beat_without_hold_keeps_its_pulses(project, monkeypatch):
    no_print(monkeypatch)
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    layout.apply_segment(seg, tl, d)
    last = seg.gsap_scene_config.camera_path[-1]
    assert last.pulse is None or (isinstance(last.pulse, list) and any(last.pulse)) or last.pulse > 0
    assert severity.hold_ranges(tl) == []


def test_bad_unknown_waived_gate_is_rejected(project):
    with pytest.raises(EngineError, match="gates.waive"):
        project(two_maps(gates={"waive": ["X99"]}))


# --------------------------------------------------------------------------------------------- not_checked
def test_good_not_checked_lists_skips_not_run_gates_and_profile_switches(project):
    _, tl = project()
    fs = [Finding("R38", "pass", "fine"), Finding("O01", "skip", "no drawn overlays"),
          Finding("R63", "skip", "not run: DOM stage off", 2), Finding("M02", "warn", "x", 2, waived="fail: lowered")]
    nc = severity.not_checked(fs, tl)
    assert nc[:2] == [{"gate": "O01", "seg": None, "kind": "skipped", "why": "no drawn overlays"},
                      {"gate": "R63", "seg": 2, "kind": "not_run", "why": "DOM stage off"}]
    off = {e["gate"] for e in nc if e["kind"] == "off"}                    # the default profile: no channel gates
    assert off == {g.id for g in reg.GATES if gate_tools.effect(g, profiles.of(tl)) == "off"} and "S01" in off
    assert "AIQA" not in off and "M02" not in {e["gate"] for e in nc}       # on request / waived stay elsewhere


def test_bad_not_checked_keeps_out_what_ran(project):
    _, tl = project()
    assert severity.not_checked([Finding("T02", "fail", "gap"), Finding("R38", "pass", "fine")]) == []
    assert all(e["kind"] == "off" for e in severity.not_checked([Finding("T02", "fail", "gap")], tl))


def test_good_preflight_without_the_dom_stage_names_each_gate_that_did_not_run(project):
    """A pre-flight without the DOM stage and the snapshots names every gate of them the profile switches on."""
    from video_engine.gates import run_preflight
    p, tl = project()
    rep = run_preflight(tl, p, None, dom=False, snapshots=False)
    nc = severity.not_checked(rep.findings, tl)
    not_run = {e["gate"] for e in nc if e["kind"] == "not_run"}
    assert {"R63", "E01", "R38", "R38space"} <= not_run and "R64" not in not_run      # R64 is off in default
    assert rep.ok


def test_bad_preflight_stopped_by_T02_names_the_later_gates(project):
    from video_engine.gates import run_preflight
    p, tl = project()
    tl.segments[1].start_frame = 61                                               # a gap: T02 fails
    rep = run_preflight(tl, p, None, dom=False, snapshots=False, ai_qa=object())
    nc = {e["gate"]: e for e in severity.not_checked(rep.findings, tl) if e["kind"] == "not_run"}
    assert not rep.ok and {"M01", "R56", "R63", "R38", "AIQA"} <= set(nc) and "S01" not in nc
    assert nc["M01"]["why"] == "pre-flight stopped after layer 1 because T02 failed"
