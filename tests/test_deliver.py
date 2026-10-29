"""Delivery naming and part splitting (dry run only)."""
import pytest

from video_engine import deliver
from video_engine.util import EngineError


def test_good_names(project):
    _, tl = project()
    n = deliver.names(tl)
    assert n["root"] == "07 - 2026-10-28 - Uji Coba"
    assert n["final_dir"].endswith("/05 Final") and n["assets_dir"].endswith("/03 Aset")
    assert n["final"] == "07 - Uji Coba - final.mp4" and n["join"] == "07 - JOIN.txt" and n["credits"] == "07 - credits.txt"


def test_bad_names_missing_settings(project):
    _, tl = project(lambda d: d["settings"].pop("topic"))
    with pytest.raises(EngineError, match="topic"):
        deliver.names(tl)


def test_good_topic_cleaned(project):
    _, tl = project(lambda d: d["settings"].update(topic='Uji/Coba: "x"  y'))
    assert "/" not in deliver.names(tl)["final"] and ":" not in deliver.names(tl)["final"]


@pytest.mark.parametrize("size,parts", [(10, 1), (95, 1), (96, 2), (300, 4)])
def test_good_split_ranges(size, parts):
    r = deliver.split_ranges(size, 95)
    assert len(r) == parts and r[0][0] == 0 and r[-1][1] == size
    assert all(b - a <= 95 for a, b in r) and all(r[i][1] == r[i + 1][0] for i in range(len(r) - 1))


def test_bad_split_empty():
    with pytest.raises(EngineError):
        deliver.split_ranges(0)


def test_good_plan_parts_and_join(project, tmp_path):
    p, tl = project()
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x" * 250)
    pl = deliver.plan(tl, p, final, part_max=100)
    parts = [f for f in pl.files if f["range"]]
    assert len(parts) == 3 and parts[0]["name"].endswith(".part1") and pl.dry_run
    assert "cat " in pl.join_txt and "copy /b" in pl.join_txt
    assert any(f["name"] == "07 - credits.txt" for f in pl.files)


def test_bad_plan_no_final(project, tmp_path):
    p, tl = project()
    with pytest.raises(EngineError, match="not found"):
        deliver.plan(tl, p, tmp_path / "missing.mp4")


# Audit 2026-10-25 item 11: a file of exactly the part limit gave one ".part0" while JOIN.txt named another file.
# Rule: size <= limit -> one file under the final name, no parts; size > limit -> ".part1".."partN", each <= limit,
# and JOIN.txt names exactly those parts in order.
def test_good_plan_exactly_at_limit_is_one_unsplit_file(project, tmp_path):
    p, tl = project()
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x" * 100)
    pl = deliver.plan(tl, p, final, part_max=100)
    vids = [f for f in pl.files if f["source"] == str(final)]
    assert [(f["name"], f["bytes"], f["range"]) for f in vids] == [("07 - Uji Coba - final.mp4", 100, None)]
    assert ".part" not in pl.join_txt and "No join needed" in pl.join_txt


@pytest.mark.parametrize("size", [101, 200, 201])
def test_bad_plan_one_byte_over_limit_splits_and_join_names_every_part(project, tmp_path, size):
    p, tl = project()
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x" * size)
    pl = deliver.plan(tl, p, final, part_max=100)
    parts = [f for f in pl.files if f["range"]]
    names = [f["name"] for f in parts]
    assert names == [f"07 - Uji Coba - final.mp4.part{i + 1}" for i in range(len(parts))] and len(parts) >= 2
    assert all(f["bytes"] <= 100 for f in parts) and sum(f["bytes"] for f in parts) == size
    assert pl.join_txt.count(".part") == 2 * len(parts)            # every part once in the cat and once in copy /b
    assert " ".join(f'"{n}"' for n in names) in pl.join_txt and ".part0" not in pl.join_txt


def test_good_default_limit_boundary_95_000_000():
    assert deliver.PART_MAX_BYTES == 95_000_000
    assert deliver.split_ranges(95_000_000) == [(0, 95_000_000)]
    assert deliver.split_ranges(95_000_001) == [(0, 47_500_001), (47_500_001, 95_000_001)]
