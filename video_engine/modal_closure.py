"""Files a final build needs, and where they land in the container.

The project directory is often outside the engine checkout. Assets, the voiceover and
locked sounds are symlinks into a library or the sound cache. This module lists that
closure, follows symlinks, and plans container paths. The timeline JSON is not rewritten:
library roots are remapped with VIDEO_ENGINE_* variables, and an absolute path named by
the timeline is recreated in the container as a symlink to the uploaded file.

Nothing here imports the native extension or talks to Modal.
"""
from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Same filenames as video_engine/geodata.py admin0() and video_engine/gates/geo.py _land_file().
_GEO_BASE = Path("natural-earth") / "natural-earth-vector" / "geojson"
_LAND_NAMES = ("ne_50m_land.geojson", "ne_10m_land.geojson", "ne_110m_land.geojson")
_ADMIN_NAMES = ("ne_50m_admin_0_countries.geojson", "ne_10m_admin_0_countries.geojson",
                "ne_110m_admin_0_countries.geojson")

_SCHEMES = ("open:", "project:", "vendor:", "download:", "cache:", "engine:")
_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".tif", ".tiff", ".avif",
    ".mp4", ".mov", ".webm", ".mkv", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
    ".json", ".geojson", ".csv", ".cube", ".lut", ".3dl",
    ".ttf", ".otf", ".woff", ".woff2", ".ass", ".srt", ".vtt", ".md", ".toml", ".txt",
}
# Recreating a timeline absolute path must not replace a system file.
_LINK_DENY = {"bin", "boot", "dev", "etc", "lib", "lib64", "proc", "root", "run", "sbin", "sys", "usr", "opt", "src", "work"}

_MEDIA_SIDE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv",
               ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_CAPTION_SIDE = (".captions.json", ".words.json", ".ass", ".srt", ".vtt")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FileRef:
    role: str
    logical: str
    local: str
    root: str
    rel: str
    sha256: str
    size: int
    symlink: bool
    ship: bool
    dest: str
    link: str = ""

    def to_dict(self) -> dict:
        return {
            "role": self.role, "logical": self.logical, "local": self.local, "root": self.root,
            "rel": self.rel, "sha256": self.sha256, "size": self.size, "symlink": self.symlink,
            "ship": self.ship, "dest": self.dest, "link": self.link,
        }


@dataclass(frozen=True)
class Root:
    key: str
    env_var: str | None
    local: Path
    remote: str
    ship: bool


@dataclass
class Closure:
    project: str
    files: list[FileRef] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    roots: list[dict] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    links: list[dict] = field(default_factory=list)
    mkdir: list[str] = field(default_factory=list)
    digest: str = ""
    upload_bytes: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing

    def layout(self, run_id: str, resources: dict | None = None, toolchain: dict | None = None) -> dict:
        return {
            "version": 1, "run_id": run_id, "project": self.project, "digest": self.digest,
            "env": self.env, "mkdir": self.mkdir, "files": [f.to_dict() for f in self.files],
            "links": self.links, "resources": resources or {}, "local_toolchain": toolchain or {},
        }


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def _env_path(env: dict, key: str, default: Path, explicit: bool) -> Path:
    if env.get(key):
        return Path(env[key])
    if explicit:
        return Path(f"/unset-{key}")
    return default


def _has_symlink(path: Path, stop: Path | None = None) -> bool:
    cur = path
    stop_abs = stop.absolute() if stop is not None else None
    stop_r = stop.resolve() if stop is not None else None
    while True:
        if cur.is_symlink():
            return True
        if stop_r is not None and stop_abs is not None:
            try:
                if cur.resolve() == stop_r or cur.absolute() == stop_abs:
                    return False
            except OSError:
                pass
        parent = cur.parent
        if parent == cur:
            return False
        cur = parent


def link_allowed(absolute: str) -> bool:
    """An absolute timeline path may be recreated in the container when it is not a system location."""
    parts = Path(absolute).parts
    if len(parts) < 2 or parts[0] != "/":
        return False
    return parts[1] not in _LINK_DENY


def _workspace(env: dict, explicit: bool) -> Path:
    if env.get("VIDEO_ENGINE_WORKSPACE"):
        return Path(env["VIDEO_ENGINE_WORKSPACE"])
    if explicit:
        return Path("/unset-VIDEO_ENGINE_WORKSPACE")
    from . import discover
    return discover.default_workspace(env)


def _roots(project: Path, env: dict, engine_root: Path, profiles_dir: Path, cache_dir: Path,
           vendor: Path, explicit: bool) -> list[Root]:
    workspace = _workspace(env, explicit)

    def one(key: str, var: str | None, local: Path, remote: str, ship: bool) -> Root:
        return Root(key, var, Path(local), remote, ship)

    out = [
        one("workspace", "VIDEO_ENGINE_WORKSPACE", workspace, "/work/roots/workspace", True),
        one("geo-library", "VIDEO_ENGINE_GEO_LIBRARY",
            _env_path(env, "VIDEO_ENGINE_GEO_LIBRARY", workspace / "geo-library", explicit),
            "/work/roots/geo-library", True),
        one("open-assets", "VIDEO_ENGINE_OPEN_ASSETS",
            _env_path(env, "VIDEO_ENGINE_OPEN_ASSETS", workspace / "open-assets", explicit),
            "/work/roots/open-assets", True),
        one("downloads", "VIDEO_ENGINE_DOWNLOAD_DIR",
            _env_path(env, "VIDEO_ENGINE_DOWNLOAD_DIR", workspace / "downloads", explicit),
            "/work/roots/downloads", True),
    ]
    raw_stock = env.get("VIDEO_ENGINE_STOCK_DIRS")
    stocks = [Path(p) for p in raw_stock.split(os.pathsep) if p] if raw_stock else [workspace / "asset-cache"]
    for i, folder in enumerate(stocks):
        key = "stock" if len(stocks) == 1 else f"stock-{i}"
        out.append(one(key, "VIDEO_ENGINE_STOCK_DIRS", folder, f"/work/roots/{key}", True))
    raw_extra = env.get("VIDEO_ENGINE_ASSET_ROOTS")
    if raw_extra:
        extras = [Path(p) for p in raw_extra.split(os.pathsep) if p]
        for i, folder in enumerate(extras):
            key = "asset" if len(extras) == 1 else f"asset-{i}"
            out.append(one(key, "VIDEO_ENGINE_ASSET_ROOTS", folder, f"/work/roots/{key}", True))
    out.append(one("cache", "VIDEO_ENGINE_CACHE", cache_dir, "/work/cache", True))
    vendor_ship = not _inside(vendor, engine_root)
    vendor_remote = "/work/roots/vendor" if vendor_ship else "/src/" + _rel_under(vendor, engine_root)
    out.append(one("vendor", "VIDEO_ENGINE_VENDOR", vendor, vendor_remote, vendor_ship))
    profiles_ship = not _inside(profiles_dir, engine_root)
    if profiles_ship:
        profiles_remote = "/work/roots/profiles"
    else:
        profiles_remote = "/src/" + _rel_under(profiles_dir, engine_root)
    out.append(one("profiles", "VIDEO_ENGINE_PROFILES", profiles_dir, profiles_remote, profiles_ship))
    out.append(one("engine", None, engine_root, "/src", False))
    return out


def _rel_under(path: Path, parent: Path) -> str:
    try:
        return path.resolve().relative_to(parent.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def _container_env(roots: list[Root]) -> tuple[dict[str, str], list[str]]:
    by: dict[str, list[Root]] = {}
    for root in roots:
        if root.env_var:
            by.setdefault(root.env_var, []).append(root)
    env: dict[str, str] = {"VIDEO_ENGINE_SHM": "/work/shm"}
    mkdir = ["/work/project", "/work/shm", "/work/abs"]
    for var, group in by.items():
        env[var] = os.pathsep.join(r.remote for r in group)
        for r in group:
            if r.remote not in mkdir:
                mkdir.append(r.remote)
    return env, mkdir


def _place(lexical: Path, project: Path, roots: list[Root]) -> tuple[str, str, bool, str]:
    """(root key, relative posix path, ship, container dest). Project layout wins over a library
    that happens to contain the same resolved bytes."""
    lex = lexical.absolute()
    try:
        rel = lex.relative_to(project.absolute())
    except ValueError:
        rel = None
    if rel is not None and ".." not in rel.parts:
        return "project", rel.as_posix(), True, "/work/project/" + rel.as_posix()
    best: tuple[int, Root, Path] | None = None
    for root in roots:
        base = root.local.absolute()
        try:
            under = lex.relative_to(base)
        except ValueError:
            continue
        if ".." in under.parts:
            continue
        if best is None or len(base.as_posix()) > best[0]:
            best = (len(base.as_posix()), root, under)
    if best is not None:
        root, under = best[1], best[2]
        dest = root.remote.rstrip("/") + "/" + under.as_posix()
        return root.key, under.as_posix(), root.ship, dest
    rel_abs = lex.as_posix().lstrip("/")
    return "absolute", rel_abs, True, "/work/abs/" + rel_abs


class _Builder:
    def __init__(self, project: Path, roots: list[Root]):
        self.project = project
        self.roots = roots
        self.files: dict[tuple[str, str], FileRef] = {}
        self.missing: list[dict] = []

    def add_missing(self, ref: str, role: str, reason: str) -> None:
        self.missing.append({"ref": ref, "role": role, "reason": reason})

    def add_path(self, lexical: Path, role: str, ref: str) -> None:
        try:
            if lexical.is_symlink() and not lexical.exists():
                self.add_missing(ref, role, "dangling symlink")
                return
            if not lexical.exists():
                self.add_missing(ref, role, "not found")
                return
            real = lexical.resolve(strict=True)
        except OSError as exc:
            self.add_missing(ref, role, str(exc))
            return
        if not real.is_file():
            self.add_missing(ref, role, "not a file")
            return
        root, rel, ship, dest = _place(lexical, self.project, self.roots)
        link = ref if ref.startswith("/") and link_allowed(ref) and ref != dest else ""
        # A file that lives in the image still needs a symlink when the timeline names the host path.
        if not ship and link:
            pass
        key = (root, rel)
        symlink = _has_symlink(lexical, self.project)
        try:
            digest = file_sha256(real)
            size = real.stat().st_size
        except OSError as exc:
            self.add_missing(ref, role, str(exc))
            return
        current = self.files.get(key)
        if current is not None:
            if role not in current.role.split("+"):
                current.role = current.role + "+" + role
            if symlink:
                current.symlink = True
            if link and not current.link:
                current.link = link
            return
        self.files[key] = FileRef(
            role=role, logical=ref, local=str(real), root=root, rel=rel, sha256=digest, size=size,
            symlink=symlink, ship=ship, dest=dest, link=link,
        )

    def add_existing(self, lexical: Path, role: str, ref: str) -> None:
        if lexical.exists() or lexical.is_symlink():
            self.add_path(lexical, role, ref)


def _scheme_path(ref: str, tail: str, project: Path, roots: list[Root]) -> Path | None:
    by = {r.key: r for r in roots}
    if ref.startswith("project:"):
        return project / tail
    if ref.startswith("open:"):
        root = by.get("open-assets")
        return (root.local / tail) if root else None
    if ref.startswith("vendor:"):
        root = by.get("vendor")
        return (root.local / tail) if root else None
    if ref.startswith("cache:"):
        root = by.get("cache")
        return (root.local / tail) if root else None
    if ref.startswith("engine:"):
        root = by.get("engine")
        return (root.local / tail) if root else None
    if ref.startswith("download:"):
        stocks = [r for r in roots if r.key == "stock" or r.key.startswith("stock-")]
        downloads = [r for r in roots if r.key == "downloads"]
        for root in (*stocks, *downloads):
            candidate = root.local / tail
            if candidate.is_file() or candidate.is_symlink():
                return candidate
        # Fall back to the first stock root so the missing report names a concrete path.
        if stocks:
            return stocks[0].local / tail
        if downloads:
            return downloads[0].local / tail
    return None


def _looks_like_path(text: str) -> bool:
    if not text or len(text) > 512 or "\n" in text or text.startswith(("http://", "https://", "data:")):
        return False
    if text.startswith(_SCHEMES):
        return True
    if " " in text and not text.startswith("/"):
        return False
    suffix = Path(text).suffix.lower()
    if "/" in text or "\\" in text or text.startswith("/"):
        return suffix in _EXTS or suffix == ""
    return suffix in _EXTS


def _walk_strings(value, found: list[str]) -> None:
    if isinstance(value, str):
        if _looks_like_path(value):
            found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_strings(item, found)


def _voiceover(doc: dict) -> str:
    chain = doc.get("audio_chain")
    if isinstance(chain, dict) and isinstance(chain.get("voiceover_path"), str):
        return chain["voiceover_path"]
    if isinstance(doc.get("voiceover_path"), str):
        return doc["voiceover_path"]
    return ""


def _candidate(ref: str, project: Path, roots: list[Root]) -> tuple[Path | None, str | None]:
    """(lexical path, scheme-or-none). A bad scheme tail returns (None, reason)."""
    for scheme in _SCHEMES:
        if ref.startswith(scheme):
            tail = ref[len(scheme):]
            if not tail or ".." in Path(tail).parts or tail.startswith(("/", "\\")):
                return None, "invalid scheme path"
            return _scheme_path(ref, tail, project, roots), None
    if ref.startswith("/"):
        return Path(ref), None
    return project / ref, None


def _should_require(ref: str, lexical: Path | None, project: Path) -> bool:
    """A string is part of the closure when it is a scheme, an existing file, or a media path
    whose parent directory exists. Prose that merely contains a slash is left out."""
    if any(ref.startswith(s) for s in _SCHEMES):
        return True
    if lexical is not None and (lexical.is_file() or lexical.is_symlink()):
        return True
    if lexical is None:
        return False
    if lexical.suffix.lower() not in _EXTS:
        return False
    parent = lexical.parent
    return parent.exists()


def _profile_files(name: str, profiles_dir: Path) -> tuple[list[Path], list[dict]]:
    """Profile toml, its include and extends chain, plus rules_file and lut paths. Missing files
    are reported and do not raise."""
    found: list[Path] = []
    missing: list[dict] = []
    seen: set[Path] = set()

    def take(path: Path, ref: str) -> dict | None:
        if path in seen:
            return None
        seen.add(path)
        if not path.is_file():
            missing.append({"ref": ref, "role": "profile", "reason": "not found"})
            return None
        found.append(path)
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeError) as exc:
            missing.append({"ref": ref, "role": "profile", "reason": f"unreadable: {exc}"})
            return None

    def extras(table: dict, base: Path) -> None:
        strings: list[str] = []
        _collect_keys(table, {"lut", "rules_file"}, strings)
        for rel in strings:
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                continue
            if Path(rel).suffix.lower() not in _EXTS:
                continue
            extra = base / rel
            if extra.is_file():
                if extra not in seen:
                    seen.add(extra)
                    found.append(extra)
            else:
                missing.append({"ref": rel, "role": "profile", "reason": "not found"})

    default = profiles_dir / "default.toml"
    table = take(default, "default.toml")
    if table:
        extras(table, profiles_dir)
    if not name or name == "default":
        return found, missing
    chain: list[str] = []
    cur: str | None = name
    while cur:
        if cur in chain:
            missing.append({"ref": cur, "role": "profile", "reason": "extends loop"})
            break
        if len(chain) >= 8:
            missing.append({"ref": name, "role": "profile", "reason": "extends chain is too long"})
            break
        chain.append(cur)
        path = profiles_dir / f"{cur}.toml"
        table = take(path, f"{cur}.toml")
        if table is None:
            break
        extras(table, profiles_dir)
        includes = table.get("include") or []
        if isinstance(includes, str):
            includes = [includes]
        if isinstance(includes, list):
            for rel in includes:
                if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
                    missing.append({"ref": str(rel), "role": "profile", "reason": "invalid include"})
                    continue
                inc = profiles_dir / rel
                inc_table = take(inc, rel)
                if inc_table:
                    extras(inc_table, profiles_dir)
        nxt = table.get("extends")
        cur = nxt if isinstance(nxt, str) and nxt and nxt != "default" else None
    return found, missing


def _collect_keys(value, keys: set[str], found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str):
                found.append(item)
            else:
                _collect_keys(item, keys, found)
    elif isinstance(value, list):
        for item in value:
            _collect_keys(item, keys, found)


def _first_geo(root: Path, names: tuple[str, ...]) -> Path | None:
    base = root / _GEO_BASE
    for name in names:
        path = base / name
        if path.is_file():
            return path
    return None


def _sound_entries(manifest: dict) -> list[dict]:
    sounds = manifest.get("sounds")
    if not isinstance(sounds, dict):
        return []
    out = []
    music = sounds.get("music")
    if isinstance(music, dict) and music.get("path"):
        out.append(music)
    sfx = sounds.get("sfx")
    if isinstance(sfx, dict):
        for entry in sfx.values():
            if isinstance(entry, dict) and entry.get("path"):
                out.append(entry)
    return out


def _sha_ok(path: Path, sha: object) -> bool:
    if not isinstance(sha, str) or not sha:
        return True
    try:
        return file_sha256(path.resolve(strict=True)) == sha
    except OSError:
        return False


def _add_sound(builder: _Builder, entry: dict, project: Path, roots: list[Root]) -> None:
    ref = str(entry.get("path") or "")
    sha = entry.get("sha256")
    suffix = Path(ref).suffix.lower()
    copy = project / "out" / "locked-sounds" / f"{sha}{suffix}" if isinstance(sha, str) and sha else None
    lexical, bad = _candidate(ref, project, roots)
    if bad:
        builder.add_missing(ref, "sound", bad)
        return
    added = False
    mismatched = False
    if lexical is not None and (lexical.is_file() or (lexical.is_symlink() and lexical.exists())):
        if _sha_ok(lexical, sha):
            builder.add_path(lexical, "sound", ref)
            added = True
            try:
                real = lexical.resolve(strict=True)
            except OSError:
                real = None
            if real is not None:
                for side in (real.with_suffix(".json"), Path(str(real) + ".info.json")):
                    builder.add_existing(side, "sidecar", side.name)
        else:
            mismatched = True
    if copy is not None and (copy.is_file() or (copy.is_symlink() and copy.exists())) and _sha_ok(copy, sha):
        builder.add_path(copy, "sound", copy.relative_to(project).as_posix())
        added = True
    if not added:
        reason = "sha256 differs from the sound lock" if mismatched else "locked sound is not in the cache or out/locked-sounds"
        builder.add_missing(ref, "sound", reason)


def resolve_closure(project: Path, *, environ: dict | None = None, engine_root: Path | None = None,
                    profiles_dir: Path | None = None) -> Closure:
    """Closure of `project`. Pass `environ` to ignore the process environment (tests)."""
    project = project.absolute()
    explicit = environ is not None
    env = dict(os.environ if environ is None else environ)
    engine = (engine_root or Path(__file__).resolve().parents[1]).absolute()
    if profiles_dir is not None:
        profiles = Path(profiles_dir)
    else:
        profiles = _env_path(env, "VIDEO_ENGINE_PROFILES", engine / "profiles", explicit)
    cache = _env_path(env, "VIDEO_ENGINE_CACHE", engine / ".cache", explicit)
    vendor = _env_path(env, "VIDEO_ENGINE_VENDOR", engine / "vendor", explicit)
    roots = _roots(project, env, engine, profiles, cache, vendor, explicit)
    cenv, mkdir = _container_env(roots)
    builder = _Builder(project, roots)
    timeline_path = project / "master_timeline.json"
    doc: dict = {}
    if not timeline_path.is_file():
        builder.add_missing("master_timeline.json", "timeline", "not found")
    else:
        try:
            loaded = json.loads(timeline_path.read_text(encoding="utf-8"))
            doc = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            builder.add_missing("master_timeline.json", "timeline", f"unreadable: {exc}")
        builder.add_path(timeline_path, "timeline", "master_timeline.json")
    builder.add_existing(project / "credits.txt", "project", "credits.txt")
    voice = _voiceover(doc)
    captions = doc.get("captions")
    has_captions = isinstance(captions, list) and len(captions) > 0
    strings: list[str] = []
    _walk_strings(doc, strings)
    seen_refs: set[str] = set()
    for ref in strings:
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        lexical, bad = _candidate(ref, project, roots)
        if bad:
            builder.add_missing(ref, "timeline", bad)
            continue
        if not _should_require(ref, lexical, project):
            continue
        role = "captions" if has_captions and ref == voice else "timeline"
        if lexical is None:
            builder.add_missing(ref, role, "not found")
        else:
            builder.add_path(lexical, role, ref)
    if voice and has_captions:
        side_base = project / voice if not voice.startswith("/") else Path(voice)
        for suffix in _CAPTION_SIDE:
            builder.add_existing(Path(str(side_base) + suffix) if suffix.startswith(".") else side_base.with_suffix(suffix),
                                 "captions", Path(str(side_base) + suffix).name)
        # Path(str + ".captions.json") covers the dotted suffixes above. Also the stem forms.
        for suffix in _CAPTION_SIDE:
            builder.add_existing(side_base.parent / (side_base.stem + suffix), "captions", side_base.stem + suffix)
    manifest_path = project / "out" / "build_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            builder.add_missing("out/build_manifest.json", "sound", f"unreadable: {exc}")
            manifest = None
        if isinstance(manifest, dict):
            builder.add_path(manifest_path, "sound", "out/build_manifest.json")
            for entry in _sound_entries(manifest):
                _add_sound(builder, entry, project, roots)
    profile_name = doc.get("profile") if isinstance(doc.get("profile"), str) else "default"
    pfiles, pmissing = _profile_files(profile_name or "default", profiles)
    builder.missing.extend(pmissing)
    for path in pfiles:
        builder.add_path(path, "profile", str(path))
    geo = next((r for r in roots if r.key == "geo-library"), None)
    if geo is not None and geo.local.is_dir():
        for names in (_LAND_NAMES, _ADMIN_NAMES):
            hit = _first_geo(geo.local, names)
            if hit is not None:
                builder.add_path(hit, "geo", str(hit))
    # Registry rows the asset resolver reads when a shipped file comes from that library.
    open_root = next((r for r in roots if r.key == "open-assets"), None)
    if open_root is not None and any(f.root == "open-assets" for f in builder.files.values()):
        builder.add_existing(open_root.local / "catalog.csv", "sidecar", "catalog.csv")
    for root in roots:
        if root.key == "stock" or root.key.startswith("stock-"):
            if any(f.root == root.key for f in builder.files.values()):
                builder.add_existing(root.local / "index.csv", "sidecar", "index.csv")
    for media in list(builder.files.values()):
        if Path(media.local).suffix.lower() not in _MEDIA_SIDE:
            continue
        real = Path(media.local)
        for side in (real.with_suffix(".json"), Path(str(real) + ".info.json")):
            builder.add_existing(side, "sidecar", side.name)
    files = sorted(builder.files.values(), key=lambda f: (f.root, f.rel, f.logical))
    links = [{"at": f.link, "dest": f.dest} for f in files if f.link]
    blobs: dict[str, int] = {}
    for item in files:
        if item.ship:
            blobs.setdefault(item.sha256, item.size)
    payload = "\n".join(f"{item.root}\t{item.rel}\t{item.sha256}\t{item.size}" for item in files if item.ship)
    digest = _sha_bytes(payload.encode())
    return Closure(
        project=str(project), files=files, missing=builder.missing,
        roots=[{"key": r.key, "env_var": r.env_var, "local": str(r.local), "remote": r.remote, "ship": r.ship}
               for r in roots],
        env=cenv, links=links, mkdir=mkdir, digest=digest, upload_bytes=sum(blobs.values()),
    )


def materialize(layout: dict, cas_root: Path) -> None:
    """Copy content-addressed blobs into the container tree and recreate absolute links.

    `cas_root` holds `<sha[:2]>/<sha>` files. Each shipped file is checked against its sha256
    after the copy. The timeline file is not edited.
    """
    for directory in layout.get("mkdir") or []:
        Path(directory).mkdir(parents=True, exist_ok=True)
    for item in layout.get("files") or []:
        if not item.get("ship"):
            continue
        sha = item["sha256"]
        src = Path(cas_root) / sha[:2] / sha
        dest = Path(item["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        _copy_verified(src, dest, sha)
    for link in layout.get("links") or []:
        at = Path(link["at"])
        if not link_allowed(str(at)):
            continue
        at.parent.mkdir(parents=True, exist_ok=True)
        if at.is_symlink() or at.exists():
            at.unlink()
        at.symlink_to(link["dest"])


def _copy_verified(src: Path, dest: Path, sha: str) -> None:
    import shutil
    shutil.copyfile(src, dest)
    got = file_sha256(dest)
    if got != sha:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"{dest} sha256 {got} != {sha}")
