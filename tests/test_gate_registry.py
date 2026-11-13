"""Gate registry (video_engine/gate_registry.py) and the read-only `engine gates` tools (gate_tools.py)."""
import json
import re
from pathlib import Path

import pytest

from video_engine import cli, gate_registry as reg, gate_tools, profiles

ROOT = Path(__file__).resolve().parents[1]
GATE_ID = re.compile(r'"((?:[A-Z]\d{2}|AS\d{2}|AIQA|GW01|R56px|R38space))"')
# Source files that create findings (a gate id in quotes anywhere in them is a gate id).
SOURCES = [*(ROOT / "video_engine" / "gates").glob("*.py"), ROOT / "video_engine" / "qa.py",
           ROOT / "video_engine" / "ai" / "tasks.py", ROOT / "video_engine" / "build.py",
           ROOT / "video_engine" / "audio.py"]

# The id lists before they were derived from the registry (frozen here as the regression baseline).
OLD_STYLE = {"H01", "C01", "C02", "V01", "N01", "P01", "P02", "K01", "K02", "D01", "D02", "S01",
             "AS01", "AS02", "AS03", "AS05", "AS06"}
# KERNEL_GATES defines the 8 non-negotiable integrity gates.
KERNEL_GATES = {"T02", "M01", "W01", "L22", "R38", "A01", "G01", "M03"}


def test_good_registry_ids_are_unique_and_every_layer_is_known():
    ids = [g.id for g in reg.GATES]
    assert len(ids) == len(set(ids))
    assert {g.layer for g in reg.GATES} <= set(reg.LAYERS)
    assert {g.kind for g in reg.GATES} == {"technical", "style"}
    assert all(g.title and g.summary for g in reg.GATES)


def test_good_derived_id_lists_match_the_lists_before_the_registry():
    assert set(reg.STYLE_GATE_IDS) == OLD_STYLE | {"P03", "R38space"} and len(reg.STYLE_GATE_IDS) == 19
    assert len(reg.TECHNICAL_GATE_IDS) == 42 and "U01" in reg.TECHNICAL_GATE_IDS and "AS04" in reg.TECHNICAL_GATE_IDS
    assert set(reg.KERNEL_GATE_IDS) == KERNEL_GATES
    assert KERNEL_GATES <= set(reg.NEVER_ADVISORY)  # kernel gates are a subset of the 12 never-advisory gates
    assert reg.KNOWN_GATE_IDS == reg.STYLE_GATE_IDS + reg.TECHNICAL_GATE_IDS
    assert set(reg.ANTI_SLOP_IDS) == {f"AS0{i}" for i in range(1, 7)}
    assert not {"AIQA", "GW01"} & set(reg.KNOWN_GATE_IDS)      # not waivable / advisory-capable


def test_good_every_gate_id_the_code_emits_is_registered():
    emitted = {m for f in SOURCES for m in GATE_ID.findall(f.read_text(encoding="utf-8"))}
    assert emitted - set(reg.BY_ID) == set(), "gate ids in the code but not in gate_registry.py"


def test_bad_a_registered_gate_that_no_code_emits_is_stale():
    emitted = {m for f in SOURCES for m in GATE_ID.findall(f.read_text(encoding="utf-8"))}
    # L01..L10 are keys of one table in layout_gates.py; everything else must appear in the sources.
    assert set(reg.BY_ID) - emitted == set(), "registered gates that no module emits"


def test_good_geoverum_effects_match_the_profile():
    p = profiles.load("geoverum")
    eff = {g.id: gate_tools.effect(g, p) for g in reg.GATES}
    assert all(eff[g.id] != "blocks" for g in reg.GATES if g.kind == "style")   # no style gate blocks by default on Geoverum
    assert eff["S01"] == "warns"
    assert eff["H01"] == "warns" and eff["P01"] == "warns" and eff["C02"] == "warns"
    assert eff["C01"] == eff["V01"] == eff["D02"] == "off"     # tables absent in geoverum.toml
    assert eff["R64"] == "warns" and eff["L02"] == "warns"     # advisory in geoverum.toml
    assert eff["L06"] == eff["R63"] == eff["Z01"] == "warns"   # layout/placement advisory in geoverum.toml
    assert eff["T02"] == eff["A01"] == "blocks"   # kernel gates always block
    assert eff["R07"] == eff["R14"] == "warns"   # classified as lint gates (warn unless required)
    assert eff["A02"] == "off"   # off when require_source is unset
    assert eff["AS01"] == "warns"                       # anti-slop stays warn
    assert eff["AIQA"] == "on request" and "GW01" not in reg.BY_ID and "GW01" not in eff  # No meta-gate finding is emitted for waivers


def test_good_default_profile_has_no_channel_gates_and_no_layout_gates():
    p = profiles.load("default")
    for gid in ("H01", "C01", "N01", "S01", "K02", "R64", "L01", "T01", "B01", "Z01", "A02"):
        assert gate_tools.effect(reg.BY_ID[gid], p) == "off", gid
    assert gate_tools.effect(reg.BY_ID["R63"], p) == "warns"  # R63 is classified as a lint gate (warns by default)
    assert gate_tools.effect(reg.BY_ID["AS02"], p) == "warns"


def test_good_select_filters_and_rejects_unknown_names():
    assert [g.id for g in gate_tools.select(["r63", "S01"])] == ["R63", "S01"]
    assert {g.layer for g in gate_tools.select(layer="props")} == {"props"}
    assert {g.kind for g in gate_tools.select(kind="style")} == {"style"}
    with pytest.raises(ValueError, match="unknown gate"):
        gate_tools.select(["R99"])
    with pytest.raises(ValueError, match="unknown layer"):
        gate_tools.select(layer="nope")


def test_good_markdown_block_lists_every_gate_once():
    md = gate_tools.render_markdown()
    assert md.startswith(gate_tools.MD_BEGIN) and md.rstrip().endswith(gate_tools.MD_END)
    for g in reg.GATES:
        assert len(re.findall(rf"^\| {g.id} \|", md, re.M)) == 1, g.id


def test_good_docs_gates_embeds_the_generated_index_unchanged():
    """docs/GATES.md holds `engine gates --format md` between its markers; regenerate it when the registry changes."""
    text = (ROOT / "docs" / "GATES.md").read_text(encoding="utf-8")
    a, b = text.index(gate_tools.MD_BEGIN), text.index(gate_tools.MD_END) + len(gate_tools.MD_END)
    assert text[a:b] == gate_tools.render_markdown(), "docs/GATES.md is stale: run `engine gates --format md`"


def test_good_docs_gate_counts_and_lint_list_match_registry():
    """Every count and gate id listed in the lint section of docs/GATES.md / SPEC.md stays in sync with gate_registry."""
    gates_md = (ROOT / "docs" / "GATES.md").read_text(encoding="utf-8")
    head = gates_md.split(gate_tools.MD_BEGIN)[0]
    assert f"warn by default, {len(reg.LINT_GATE_IDS)} gates" in head
    assert f"`KERNEL_GATE_IDS` ({len(reg.KERNEL_GATE_IDS)}) and `LINT_GATE_IDS` ({len(reg.LINT_GATE_IDS)})" in head
    missing = [gid for gid in reg.LINT_GATE_IDS if f"`{gid}`" not in head]
    assert not missing, f"LINT_GATE_IDS missing from docs/GATES.md summary: {missing}"
    spec_md = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert f"the {len(reg.LINT_GATE_IDS)} non-kernel gates in `LINT_GATE_IDS`" in spec_md


def _report(path: Path, ok: bool, fails=(), warns=(), waived=()):
    rows = lambda gs, key: [{"gate": g, "level": key, "message": "x"} for g in gs]   # noqa: E731
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ok": ok, "gates": {"required": [], "fails": rows(fails, "fail"),
                                                    "warns": rows(warns, "warn"), "waived": rows(waived, "info")}}))
    return path


def test_good_stats_count_fails_warns_and_waived_per_gate(tmp_path):
    _report(tmp_path / "a" / "out" / "build_report.json", True, warns=["R63", "H01"], waived=["P01"])
    _report(tmp_path / "a" / "out" / "build_report.failed.json", False, fails=["S01"], warns=["H01"])
    _report(tmp_path / "b" / "out" / "build_report.json", True, warns=["H01", "H01"])
    (tmp_path / "c" / "out").mkdir(parents=True)
    (tmp_path / "c" / "out" / "build_report.failed.json").write_text(json.dumps({"ok": False, "error": "boom"}))
    s = gate_tools.stats(gate_tools.collect_reports([tmp_path]))
    assert s["summary"] == {"reports": 4, "ok": 2, "gate_failed": 1, "stage_errors": 1, "unreadable": 0}
    by = {r["gate"]: r for r in s["gates"]}
    assert (by["H01"]["warns"], by["H01"]["reports"]) == (4, 3)
    assert by["S01"]["fails"] == 1 and by["P01"]["waived"] == 1
    assert s["gates"][0]["gate"] == "H01"                 # most findings first
    assert "R38" in s["never_seen"] and "H01" not in s["never_seen"]
    assert "H01" in gate_tools.render_stats(s)


def test_good_collect_reports_accepts_files_projects_and_out_folders(tmp_path):
    f = _report(tmp_path / "p" / "out" / "build_report.json", True)
    assert gate_tools.collect_reports([f]) == [f]
    assert gate_tools.collect_reports([tmp_path / "p"]) == [f]
    assert gate_tools.collect_reports([tmp_path / "p" / "out"]) == [f]
    assert gate_tools.collect_reports([tmp_path, f]) == [f]       # no duplicates
    with pytest.raises(FileNotFoundError):
        gate_tools.collect_reports([tmp_path / "missing"])


def test_bad_stats_skip_unreadable_reports(tmp_path):
    bad = tmp_path / "x" / "out" / "build_report.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json")
    s = gate_tools.stats([bad])
    assert s["summary"]["unreadable"] == 1 and s["summary"]["reports"] == 0
    assert "no gate findings" in gate_tools.render_stats(s)


def test_good_cli_gates_lists_describes_and_reports_stats(tmp_path, capsys):
    assert cli.main(["gates", "--kind", "style"]) == 0
    out = capsys.readouterr().out
    assert "H01" in out  # Style gate filter includes H01; header prints LEVELS summary
    assert cli.main(["gates", "r63", "--profile", "geoverum"]) == 0
    out = capsys.readouterr().out
    assert "Element margins and collisions" in out and "profile: warns" in out
    assert cli.main(["gates", "R07", "--profile", "geoverum"]) == 0
    out = capsys.readouterr().out
    assert "tier: LINT  (never advisory)" in out and "profile: warns" in out
    assert cli.main(["gates", "T02", "--profile", "geoverum"]) == 0
    out = capsys.readouterr().out
    assert "tier: KERNEL  (mandatory: cannot be lowered)" in out and "profile: blocks" in out
    assert cli.main(["gates", "--format", "json", "--layer", "props"]) == 0
    out = capsys.readouterr().out
    out = out[out.index("["):]
    assert [r["id"] for r in json.loads(out)] == ["F01", "F02", "F03", "F04"]
    _report(tmp_path / "p" / "out" / "build_report.json", True, warns=["N01"])
    assert cli.main(["gates", "--stats", str(tmp_path)]) == 0
    assert "N01" in capsys.readouterr().out


def test_bad_cli_gates_unknown_id_and_conflicting_options_are_clean_errors(tmp_path, capsys):
    assert cli.main(["gates", "R99"]) == 1
    assert "unknown gate" in capsys.readouterr().err
    assert cli.main(["gates", "--stats", str(tmp_path / "missing")]) == 1
    assert "no such file" in capsys.readouterr().err
    assert cli.main(["gates", "--stats", str(tmp_path), "--profile", "geoverum"]) == 1
    assert cli.main(["gates", "--format", "md", "--layer", "props"]) == 1
    capsys.readouterr()
    assert cli.main(["gates", "--stats", str(tmp_path)]) == 1          # a folder without reports exits 1
