"""The shipped examples/ project is buildable end to end. examples/minimal/prepare.sh downloads real OpenStreetMap
tiles (tile.openstreetmap.org) for its map, so these tests need network: when that download fails they skip with the
reason; any other prepare.sh failure (for example a map too dark for R38) still fails."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from video_engine import build, config

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "minimal"


def _prepare(dst: Path) -> None:
    """Run the example's prepare.sh; skip when its OpenStreetMap tile download fails (no network)."""
    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    r = subprocess.run(["sh", str(dst / "prepare.sh")], capture_output=True, text=True, env=env)
    if r.returncode and "gagal unduh ubin OSM" in r.stderr:
        pytest.skip("examples/minimal/prepare.sh needs network: " + r.stderr.strip().splitlines()[-1])
    if r.returncode:
        raise AssertionError(f"prepare.sh failed ({r.returncode}): {r.stderr.strip()}")


@pytest.mark.skipif(not EXAMPLE.is_dir(), reason="examples/minimal is missing")
def test_good_minimal_example_passes_preflight_without_the_toolchain(tmp_path):
    """Every gate that needs no browser passes on the shipped example (credits, licences, map resolution, captions)."""
    from video_engine import cli
    dst = tmp_path / "minimal"
    shutil.copytree(EXAMPLE, dst)
    _prepare(dst)
    assert cli.main(["preflight", str(dst), "--no-dom"]) == 0


@pytest.mark.skipif(not EXAMPLE.is_dir(), reason="examples/minimal is missing")
@pytest.mark.skipif(not config.NODE.is_file() or not config.CHROME.is_file(),
                    reason="vendor node/Chrome not installed")
def test_good_minimal_example_builds(tmp_path):
    dst = tmp_path / "minimal"
    shutil.copytree(EXAMPLE, dst)
    _prepare(dst)
    r = build.build(dst)
    assert r.ok and r.final.is_file() and r.final.stat().st_size > 0
    assert (dst / "out" / "build_report.json").is_file()
