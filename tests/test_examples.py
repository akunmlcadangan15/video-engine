"""The shipped examples/ project is buildable end to end (generated local assets, no network)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from video_engine import build, config

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "minimal"


@pytest.mark.skipif(not EXAMPLE.is_dir(), reason="examples/minimal is missing")
def test_good_minimal_example_passes_preflight_without_the_toolchain(tmp_path):
    """Every gate that needs no browser passes on the shipped example (credits, licences, map resolution, captions)."""
    from video_engine import cli
    dst = tmp_path / "minimal"
    shutil.copytree(EXAMPLE, dst)
    subprocess.run(["sh", str(dst / "prepare.sh")], check=True, capture_output=True)
    assert cli.main(["preflight", str(dst), "--no-dom"]) == 0


@pytest.mark.skipif(not EXAMPLE.is_dir(), reason="examples/minimal is missing")
@pytest.mark.skipif(not config.NODE.is_file() or not config.CHROME.is_file(),
                    reason="vendor node/Chrome not installed")
def test_good_minimal_example_builds(tmp_path):
    dst = tmp_path / "minimal"
    shutil.copytree(EXAMPLE, dst)
    subprocess.run(["sh", str(dst / "prepare.sh")], check=True, capture_output=True)
    r = build.build(dst)
    assert r.ok and r.final.is_file() and r.final.stat().st_size > 0
    assert (dst / "out" / "build_report.json").is_file()
