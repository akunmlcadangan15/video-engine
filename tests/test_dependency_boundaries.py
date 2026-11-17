"""Architectural import rules apply equally to eager and function-local imports."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "video_engine"


def imports(source: str, module: str):
    package = module if module.endswith(".__init__") else module.rpartition(".")[0]
    package = package.removesuffix(".__init__")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = importlib.util.resolve_name("." * node.level + base, package)
            if base in ("video_engine", "video_engine.core", "video_engine.render",
                        "video_engine.gates", "video_engine.ai"):
                yield from (f"{base}.{alias.name}" for alias in node.names)
            else:
                yield base
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in ("import_module", "__import__") and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    yield value


def canonical(module: str) -> str:
    """Resolve flat compatibility aliases so they cannot hide forbidden edges."""
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "video_engine":
        return module
    path = ROOT / f"{parts[1]}.py"
    if path.is_file():
        source = path.read_text()
        if "sys.modules" in source or "_sys.modules" in source:
            targets = [target for target in imports(source, f"video_engine.{parts[1]}")
                       if target.startswith("video_engine.")]
            if len(targets) == 1:
                return targets[0] + ("." + ".".join(parts[2:]) if len(parts) > 2 else "")
    return module


def forbidden(layer: str, dependency: str) -> bool:
    dep = canonical(dependency)
    if not dep.startswith("video_engine."):
        return False
    if layer == "core":
        return not dep.startswith("video_engine.core.")
    if dep == "video_engine.gates.registry" or dep.startswith("video_engine.gates.registry."):
        return False
    banned = ("render", "gates") if layer == "layout" else ("render", "ai", "build", "draft", "director")
    return any(dep == f"video_engine.{name}" or dep.startswith(f"video_engine.{name}.") for name in banned)


@pytest.mark.parametrize("layer", ["core", "layout", "timeline"])
def test_package_dependency_boundaries(layer):
    violations = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        module = "video_engine." + ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for dep in imports(path.read_text(), module):
            if forbidden(layer, dep):
                violations.append(f"{path.relative_to(ROOT)} -> {dep}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "def f():\n from video_engine.render import native\n",
    "def f():\n import video_engine.render.native\n",
    "def f():\n from ..render.native import render\n",
    "def f():\n importlib.import_module('video_engine.render.native')\n",
    "def f():\n from video_engine import native\n",
])
def test_bad_function_imports_are_detected(source):
    assert any(forbidden("core", dep) for dep in imports(source, "video_engine.core.example"))


def test_good_registry_is_allowed_for_contracts():
    assert not forbidden("layout", "video_engine.gates.registry")
    assert not forbidden("timeline", "video_engine.gates.registry")
    assert forbidden("core", "video_engine.gates.registry")
    assert forbidden("layout", "video_engine.gates.runner")


def test_good_pin_registry_is_fail_closed_without_reader(tmp_path, monkeypatch):
    from video_engine.core import cache, cache_pins
    monkeypatch.setattr(cache_pins, "_readers", {})
    root = tmp_path / "cache"
    sound = root / "sound"
    sound.mkdir(parents=True)
    (sound / "pins.json").write_text("unreadable registry")
    (sound / "locked.wav").write_bytes(b"audio")
    loose = root / "loose.bin"
    loose.write_bytes(b"evict")
    result = cache.prune(max_bytes=0, root=root)
    assert set(result.skipped_pinned) == {"sound/pins.json", "sound/locked.wav"}
    assert not loose.exists()
    assert (sound / "locked.wav").exists()


def test_bad_pin_reader_failure_aborts_eviction(tmp_path, monkeypatch):
    from video_engine.core import cache, cache_pins
    monkeypatch.setattr(cache_pins, "_readers", {})
    path = tmp_path / "entry.bin"
    path.write_bytes(b"retained")

    def broken(root):
        raise RuntimeError("Cannot validate durable pins")

    cache_pins.register("sound/pins.json", broken)
    with pytest.raises(RuntimeError, match="Cannot validate"):
        cache.prune(max_bytes=0, root=tmp_path)
    assert path.exists()


def test_good_resource_budget_accepts_primitive_policy(monkeypatch):
    from video_engine.core import resources
    monkeypatch.delenv("VIDEO_ENGINE_WORKERS", raising=False)
    result = resources.budget(profile_workers=7)
    assert (result.workers, result.source) == (7, "profile")
    monkeypatch.setenv("VIDEO_ENGINE_WORKERS", "9")
    result = resources.budget(profile_workers=7)
    assert (result.workers, result.source) == (9, "env")


def test_good_hfversion_aliases_share_identity():
    from video_engine import hfversion
    from video_engine.core import hfversion as canonical_version
    from video_engine.render import hfversion as render_version
    assert hfversion is canonical_version is render_version
