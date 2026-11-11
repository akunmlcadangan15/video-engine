"""Gate <-> profile conformance compatibility layer (video_engine/gate_conformance.py) and registry tiers.

Runtime enforcement uses the 2-tier model in `gate_registry.py` (`KERNEL_GATE_IDS` vs `LINT_GATE_IDS`), while
`gate_conformance.py` and `Gate.tier` (`mandatory` / `profile` / `flexible`) provide backward-compatible reporting
without rejecting profiles that omit optional style-gate tables.
"""
import dataclasses


from video_engine import gate_conformance as conf, gate_registry as reg, gate_tools, profiles


def _with_gates(profile, **kw):
    """Copy of `profile` with its StyleGates model edited (kw are StyleGates fields)."""
    data = profile.data.model_copy(deep=True)
    for k, v in kw.items():
        setattr(data.gates, k, v)
    return dataclasses.replace(profile, data=data)


def test_good_tiers_are_derived_and_the_mandatory_core_is_frozen():
    assert set(reg.KERNEL_GATE_IDS) == {"T02", "M01", "W01", "L22", "R38", "A01", "G01", "M03"}
    tier = {g.id: g.tier for g in reg.GATES if g.known}
    assert {g for g, t in tier.items() if t == "profile"} == set(reg.STYLE_GATE_IDS)
    assert {g for g, t in tier.items() if t == "flexible"} == set(reg.TECHNICAL_GATE_IDS) - set(reg.NEVER_ADVISORY)
    assert all(tier[g] == "mandatory" for g in reg.KERNEL_GATE_IDS)
    assert not hasattr(reg, "MANDATORY_GATE_IDS") and not hasattr(reg, "PROFILE_GATE_IDS")   # one name per list


def test_good_shipped_profiles_have_no_conformance_errors():
    for name in profiles.names():
        p = profiles.load(name)
        assert conf.errors(conf.check_profile(p)) == [], f"profile {name!r} must load cleanly"


def test_good_geoverum_mandatory_core_is_on_and_flexible_list_is_intact():
    rep = conf.mandatory_report(profiles.load("geoverum"))
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []
    assert rep["flexible_advisory"] == [] and len(rep["profile_active"]) == len(reg.LINT_GATE_IDS)


def test_good_required_gate_without_its_config_table_is_no_longer_an_error():
    p = _with_gates(profiles.load("default"), require=["H01"])     # default has no [gates.hook]
    issues = conf.check_profile(p)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_timeline_require_without_config_is_no_longer_an_error():
    from video_engine.timeline import ProjectGates
    p = profiles.load("default")
    tl_gates = ProjectGates.model_validate({"require": ["D01"]})   # default has no [gates.duration]
    issues = conf.check_timeline_gates(p, tl_gates)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_gate_both_required_and_advisory_is_no_longer_an_error():
    p = _with_gates(profiles.load("default"), require=["R44"], advisory=["R44"])
    issues = conf.check_profile(p)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_mandatory_gate_in_advisory_is_no_longer_an_error():
    p = _with_gates(profiles.load("default"), advisory=["A01"])
    issues = conf.check_profile(p)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_mandatory_gate_in_require_is_no_longer_an_error():
    # StyleGates itself rejects non-style ids in `require`; the conformance check must catch it too when the
    # model is built programmatically (e.g. by a tool that bypasses the TOML validator).
    # Conformance reports KERNEL_GATE_IDS as the mandatory core.
    p = _with_gates(profiles.load("default"), advisory=[])
    data = p.data.model_copy(deep=True)
    object.__setattr__(data.gates, "require", ["T02"])
    p2 = dataclasses.replace(p, data=data)
    issues = conf.check_profile(p2)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p2)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_dead_advisory_entry_is_no_longer_a_warning():
    p = _with_gates(profiles.load("default"), advisory=["T01"])    # default has no [print], so T01 is off
    issues = conf.check_profile(p)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_required_gate_with_its_config_table_passes():
    p = profiles.load("geoverum")                                  # geoverum has [gates.hook]
    p2 = _with_gates(p, require=["H01"])
    assert conf.errors(conf.check_profile(p2)) == []


def test_good_duplicate_entries_are_no_longer_warnings():
    p = _with_gates(profiles.load("geoverum"), advisory=["R44", "R44"])
    issues = conf.check_profile(p)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(p)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_profile_load_accepts_a_required_gate_without_config(tmp_path, monkeypatch):
    """End to end: a channel profile TOML that requires C01 without [gates.cuts] loads cleanly through the compatibility shim."""
    from video_engine import config

    import shutil
    root = tmp_path / "profiles"
    root.mkdir()
    for src in (profiles.path_of("default"), profiles.path_of("geoverum")):
        (root / src.name).write_text(src.read_text(encoding="utf-8"))
    shutil.copytree(profiles.path_of("default").parent / "geoverum", root / "geoverum")
    text = (root / "geoverum.toml").read_text(encoding="utf-8")
    text = text.replace("require = []", 'require = ["C01"]')   # geoverum has no [gates.cuts]
    (root / "geoverum.toml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "PROFILES_DIR", root)
    monkeypatch.setattr(profiles, "PROFILES_DIR", root, raising=False)
    loaded = profiles.load("geoverum")
    issues = conf.check_profile(loaded)
    assert conf.errors(issues) == [] and conf.warnings(issues) == []
    rep = conf.mandatory_report(loaded)
    assert set(rep["mandatory_on"]) == set(reg.KERNEL_GATE_IDS)
    assert rep["mandatory_switch_off"] == []


def test_good_engine_gates_shows_tiers_and_the_profile_summary(capsys):
    from video_engine import cli

    assert cli.cmd_gates(_args(profile="geoverum")) == 0
    out = capsys.readouterr().out
    assert "KERNEL (error) (8)" in out  # 8 kernel integrity gates
    assert "LINT" in out
    assert "T02" in out and "KERNEL" in out
    assert "H01" in out and "LINT" in out


def _args(profile=None, id=None, layer=None, kind=None, format=None, stats=None):
    import argparse
    return argparse.Namespace(profile=profile, id=id, layer=layer, kind=kind, format=format, stats=stats)


def test_good_markdown_carries_the_tier_column():
    md = gate_tools.render_markdown()
    assert "| ID | Class | Tier | Name | Rule | Switch |" in md
    assert "| T02 | technical (mandatory) | mandatory |" in md
    assert "| H01 | style | profile |" in md
