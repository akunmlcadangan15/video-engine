"""Pin-reader contract for cache owners.

Domain readers validate durable owners and release stale pins. Before an owner
registers its reader, eviction conservatively retains that registry's subtree;
pruning in a fresh process must never depend on domain import order for safety.
Readers run under the cache root's exclusive lock and must not suppress errors.
"""
from collections.abc import Callable
from pathlib import Path

_readers: dict[str, Callable[[Path], set[str]]] = {}


def register(registry: str, reader: Callable[[Path], set[str]]) -> None:
    """Register a validator for a cache-relative durable pins.json registry."""
    path = Path(registry)
    if path.is_absolute() or ".." in path.parts or path.name != "pins.json":
        raise ValueError("Pin registries must be cache-relative pins.json paths")
    _readers[path.as_posix()] = reader


def protected_rels(root: Path) -> set[str]:
    """Return validated pins, retaining unknown registry subtrees fail-closed."""
    root = Path(root)
    keep: set[str] = set()
    registries = {p.relative_to(root).as_posix() for p in root.rglob("pins.json") if p.is_file()}
    for registry in sorted(registries | _readers.keys()):
        reader = _readers.get(registry)
        if reader is not None:
            keep.update(reader(root))
        else:
            keep.update(p.relative_to(root).as_posix()
                        for p in (root / registry).parent.rglob("*") if p.is_file())
    return keep
