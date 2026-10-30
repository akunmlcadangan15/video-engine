"""Gate severity split (video_engine/gates/severity.py): technical fails block, style gates warn unless the channel
requires them, hold:"read" intent and project waivers lower only non-required style findings. GOOD and BAD cases."""
import pytest
from pydantic import ValidationError

from video_engine import config, layout, profiles, timeline
from video_engine.gates import severity, style
from video_engine.gates.core import Finding, Report
from video_engine.util import EngineError

from test_layout import beats_doc, no_print
from test_style_gates import layout as lay, pins_every


def chan(tmp_path, monkeypatch, body: str):
    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "geoverum.toml").write_text((config.PROFILES_DIR / "geoverum.toml").read_text())
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
    rep = Report(severity.apply([Finding("C01", "fail", "3 shot changes per 10 s")], tl))
    assert [f.level for f in rep.findings] == ["warn"] and rep.ok
    assert rep.findings[0].waived.startswith("fail: style gate not required")


def test_bad_required_style_fail_still_blocks(project):
    _, tl = project(two_maps())
    rep = Report(severity.apply([hold()], tl))
    assert severity.required(tl) >= {"P01", "P02", "H01"}
    assert [f.level for f in rep.findings] == ["fail"] and not rep.ok


def test_bad_project_cannot_waive_a_channel_required_rule(project):
    _, tl = project(two_maps(gates={"waive": ["P01", "R14"]}))
    fs = severity.apply([hold(), Finding("R14", "fail", "licence not in allowlist")], tl)
    assert [f.level for f in fs if f.gate in ("P01", "R14")] == ["fail", "fail"]
    gw = [f.message for f in fs if f.gate == "GW01"]
    assert len(gw) == 2 and any("P01 is required by the channel" in m for m in gw)
    assert any("R14 is a technical gate" in m for m in gw)


def test_bad_project_hold_intent_cannot_lower_a_required_rule(project):
    _, tl = project(two_maps(seg1={"hold": "read"}))
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
    chan(tmp_path, monkeypatch, '[gates]\nrequire = ["R14"]\n')
    with pytest.raises(EngineError, match="unknown style gate"):
        profiles.load("chan")
    with pytest.raises(ValidationError, match="unknown style gate"):
        project(two_maps(gates={"require": ["X99"]}))


def test_good_geoverum_requires_camera_rules_and_p02_passes(project):
    p = profiles.load("geoverum")
    assert set(p.gates.require) == {"P01", "P02", "H01"} and p.gates.camera.min_drift_per_s == 0.06
    _, tl = project(two_maps())
    assert [f.level for f in style.p02_drift(tl, p.gates.camera)] == ["pass"]


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
    assert profiles.load().blake3 == "0aa286200acf9dcba3537be3a90895d0a41671a540b8f07c980996b8171db9fe"


def test_good_summary_lists_fails_warns_and_waived_separately(project, tmp_path, monkeypatch):
    chan(tmp_path, monkeypatch, CHAN)
    _, tl = project(two_maps("chan", gates={"waive": ["N01"]}))
    fs = severity.apply([Finding("R07", "fail", "uncredited"), Finding("C01", "fail", "rate"),
                         Finding("N01", "warn", "gap"), Finding("T02", "pass", "ok")], tl)
    s = severity.summary(fs, tl)
    assert [r["gate"] for r in s["fails"]] == ["R07"] and s["fails"][0]["class"] == "technical"
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
    assert severity.hold_ranges(tl) == [(120 + 200, 420)]


def test_bad_beat_without_hold_keeps_its_pulses(project, monkeypatch):
    no_print(monkeypatch)
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    layout.apply_segment(seg, tl, d)
    last = seg.gsap_scene_config.camera_path[-1]
    assert last.pulse is None or (isinstance(last.pulse, list) and any(last.pulse)) or last.pulse > 0
    assert severity.hold_ranges(tl) == []
