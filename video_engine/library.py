"""Local asset library: license metadata for `open-assets/` and the stock/B-roll cache.

The engine reads two local sources (overridable with VIDEO_ENGINE_OPEN_ASSETS and VIDEO_ENGINE_STOCK_DIRS):
- `open-assets/catalog.csv`: collection rows (`path,name,source,licence,files,size,attribution`).
- `asset-cache/index.csv` and per-file `.json` sidecars beside stock footage: source URL, licence and author.
`search` returns only rows whose file exists; `copy_to_project` copies one file and returns a credits_manifest row
(asset_id, path, source_url, license, author, watermark_free). No network request is made.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from . import config
from .util import EngineError

OPEN_ASSETS = Path(os.environ.get("VIDEO_ENGINE_OPEN_ASSETS", config.WORKSPACE / "open-assets"))
STOCK_DIRS = tuple(Path(x) for x in os.environ.get(
    "VIDEO_ENGINE_STOCK_DIRS",
    os.pathsep.join((str(config.WORKSPACE / "asset-cache" / "footage"),
                     str(config.WORKSPACE / "asset-cache")))).split(os.pathsep) if x)
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".png", ".webp", ".jpg", ".jpeg", ".svg"}
MODEL_EXT = {".glb", ".gltf", ".obj"}
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Asset:
    path: str
    kind: str
    name: str
    collection: str = ""
    source: str = ""
    license: str = ""
    author: str = ""
    source_url: str = ""
    bytes: int = 0

    @property
    def id(self) -> str:
        return self.path

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub("", text or ""))).strip()


def _url(text: str) -> str:
    """First http(s) URL in a catalogue source/attribution string (open-assets rows carry a repo URL)."""
    m = re.search(r"https?://[^\s,)\]]+", text or "")
    return m.group(0).rstrip(".,;") if m else ""


def _kind(path: Path, root: Path, stock: bool) -> str:
    if stock:
        return "broll"
    rel = path.relative_to(root).parts
    if not rel:
        return "asset"
    top = rel[0].lower()
    if top in {"2d", "3d"}:
        return rel[1].lower() if len(rel) > 1 else top
    if top in {"maps", "textures", "fonts", "hdri"}:
        return top
    return top


def _open_assets(root: Path) -> list[Asset]:
    cat = root / "catalog.csv"
    if not cat.is_file():
        return []
    out = []
    with cat.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rel = (row.get("path") or "").strip()
            if not rel:
                continue
            d = root / rel
            if not d.exists():
                continue
            lic = _clean(row.get("licence", ""))
            source = _clean(row.get("source", ""))
            out.append(Asset(path=str(d), kind=_kind(d, root, False), name=_clean(row.get("name") or d.name),
                             collection=rel, source=source, license=lic, author=_clean(row.get("attribution", "")),
                             source_url=_url(source),
                             bytes=d.stat().st_size if d.is_file() else 0))
    return out


def _stock_credit(path: Path) -> tuple[str, str, str]:
    """(source_url, licence, author) from a sidecar JSON, or ('', '', '')."""
    side = path.with_suffix(".json")
    if not side.is_file():
        return "", "", ""
    try:
        d = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", ""
    meta = d.get("extmetadata") or {}
    url = d.get("descriptionurl") or d.get("url") or ""
    lic = _clean((meta.get("LicenseShortName") or {}).get("value", ""))
    author = _clean((meta.get("Artist") or {}).get("value", ""))
    return url, lic, author


def _index_rows(root: Path) -> dict[str, dict]:
    for f in (root / "index.csv", root.parent / "index.csv"):
        if not f.is_file():
            continue
        try:
            with f.open(newline="", encoding="utf-8") as fh:
                return {Path(r["file"]).as_posix(): r for r in csv.DictReader(fh) if r.get("file")}
        except (OSError, KeyError, csv.Error):
            continue
    return {}


def _stock(roots: tuple[Path, ...]) -> list[Asset]:
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        rows = _index_rows(root)
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXT | IMAGE_EXT:
                continue
            rel = p.relative_to(root).as_posix()
            row = rows.get(rel) or rows.get(f"footage/{rel}") or {}
            url = (row.get("source_url") or "").strip()
            lic, author = "", ""
            if row.get("licence_credit"):
                parts = [x.strip() for x in row["licence_credit"].split("|", 1)]
                lic, author = parts[0], parts[1] if len(parts) > 1 else ""
            s_url, s_lic, s_author = _stock_credit(p)
            url, lic, author = url or s_url, lic or s_lic, author or s_author
            kind = "broll" if p.suffix.lower() in VIDEO_EXT else "photos"
            out.append(Asset(path=str(p), kind=kind, name=p.stem.replace("_", " "),
                             collection=root.name, source="stock", license=lic, author=author,
                             source_url=url, bytes=p.stat().st_size))
    return out


@lru_cache(maxsize=4)
def _index(open_root: str, stock_roots: tuple[str, ...]) -> tuple[Asset, ...]:
    seen, out = set(), []
    for a in _open_assets(Path(open_root)) + _stock(tuple(Path(x) for x in stock_roots)):
        key = str(Path(a.path).resolve())
        if key not in seen:
            seen.add(key)
            out.append(a)
    return tuple(out)


def index(open_assets_root: Path | None = None, stock_roots: tuple[Path, ...] | None = None) -> list[Asset]:
    return list(_index(str(open_assets_root or OPEN_ASSETS),
                       tuple(str(x) for x in (stock_roots if stock_roots is not None else STOCK_DIRS))))


def clear_index() -> None:
    """Drop the local index memo (tests; a caller that rewrote catalog.csv or a stock sidecar)."""
    _index.cache_clear()


def search(query: str = "", kind: str | None = None, limit: int = 50, *,
           open_assets_root: Path | None = None, stock_roots: tuple[Path, ...] | None = None) -> list[Asset]:
    tokens = [t.casefold() for t in re.findall(r"[\w-]+", query)]
    hits = []
    for a in index(open_assets_root, stock_roots):
        if kind and a.kind.casefold() != kind.casefold():
            continue
        hay = f"{a.name} {a.path} {a.collection} {a.source} {a.license}".casefold()
        if all(t in hay for t in tokens):
            hits.append(a)
        if limit > 0 and len(hits) >= limit:
            break
    return hits


def credit_for_path(path: Path, *, open_assets_root: Path | None = None,
                    stock_roots: tuple[Path, ...] | None = None) -> Asset | None:
    """The indexed asset for `path`: an exact row, or one file inside a catalogued collection directory."""
    wanted = Path(path).resolve()
    for a in index(open_assets_root, stock_roots):
        root = Path(a.path).resolve()
        if root == wanted:
            return a
        if root.is_dir() and wanted.is_file():
            try:
                wanted.relative_to(root)
            except ValueError:
                continue
            return Asset(path=str(wanted), kind=a.kind, name=wanted.stem, collection=a.collection,
                         source=a.source, license=a.license, author=a.author, source_url=a.source_url,
                         bytes=wanted.stat().st_size)
    return None


def credit_row(asset: Asset, project: Path, dst: str = "assets") -> dict:
    """Copy `asset` into `project/dst` and return its credits_manifest row. Licences without a URL need 'Own'."""
    if not asset.source_url and asset.license.lower() not in {"own", "generated"}:
        raise EngineError(f"asset {asset.path} has no source URL; its credit cannot be sourced")
    if not asset.license:
        raise EngineError(f"asset {asset.path} has no licence")
    src = Path(asset.path)
    out = Path(project) / dst
    out.mkdir(parents=True, exist_ok=True)
    target = out / src.name
    if not target.exists() or target.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, target)
    aid = re.sub(r"[^a-z0-9_]+", "_", src.stem.lower()).strip("_") or "asset"
    return {"asset_id": aid, "path": f"{dst}/{src.name}", "source_url": asset.source_url,
            "license": asset.license, "author": asset.author or None, "watermark_free": True}
