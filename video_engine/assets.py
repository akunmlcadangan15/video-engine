"""Utilities for maintaining the local asset registry."""
from __future__ import annotations

import csv
import fcntl
import json
import os
import tempfile
from pathlib import Path

from . import config, library

_MEDIA_EXT = library.VIDEO_EXT | library.IMAGE_EXT
_COLUMNS = ("file", "source_id", "source_url", "licence_credit", "first_used_project", "sha256")


def _ext_value(ext: dict, name: str) -> str:
    item = ext.get(name) or ""
    return str(item.get("value") or "") if isinstance(item, dict) else str(item)


def _metadata(path: Path) -> dict[str, str]:
    """Read only explicit metadata; names may indicate a source but never a licence."""
    result: dict[str, str] = {}
    for sidecar in (path.with_suffix(".json"), path.with_suffix(".info.json"), Path(str(path) + ".info.json")):
        if not sidecar.is_file():
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        ext = data.get("extmetadata") or {}
        if not isinstance(ext, dict):
            ext = {}

        for key, raw in {
            "source_id": data.get("source_id") or data.get("extractor_key") or data.get("extractor"),
            "source_url": data.get("source_url") or data.get("descriptionurl") or data.get("webpage_url") or data.get("url"),
            "licence": data.get("licence") or data.get("license") or _ext_value(ext, "LicenseShortName"),
            "author": data.get("author") or data.get("uploader") or _ext_value(ext, "Artist"),
            "sha256": data.get("sha256"),
        }.items():
            if raw and not result.get(key):
                result[key] = library._clean(str(raw))
    if not result.get("source_id"):
        prefix = path.stem.lower().split("_", 1)[0]
        result["source_id"] = {"px": "pexels", "ia": "internet archive", "nasa": "nasa"}.get(prefix, "")
    return result


def _backfill_rows(root: Path) -> tuple[list[str], list[dict], list[dict]]:
    index = root / "index.csv"
    if index.is_file():
        with index.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            columns = list(reader.fieldnames or ())
            existing = list(reader)
    else:
        columns, existing = [], []
    columns += [name for name in _COLUMNS if name not in columns]
    known = {row.get("file", "") for row in existing}
    additions = []
    for folder in ("footage", "photos"):
        for path in sorted((root / folder).rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _MEDIA_EXT:
                continue
            rel = path.relative_to(root).as_posix()
            if rel in known:
                continue
            meta = _metadata(path)
            licence = meta.get("licence", "")
            author = meta.get("author", "")
            additions.append({"file": rel, "source_id": meta.get("source_id", ""),
                              "source_url": meta.get("source_url", ""),
                              "licence_credit": f"{licence} | {author}" if author else licence,
                              "first_used_project": "", "sha256": meta.get("sha256", "")})
    return columns, existing, additions


def backfill(root: Path | None = None, *, dry_run: bool = False) -> dict:
    """Index existing footage/photos using sidecars, leaving unproven licences empty."""
    root = Path(root if root is not None else config.STOCK_DIRS[0])
    if dry_run:
        _, _, additions = _backfill_rows(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "index.csv.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            columns, existing, additions = _backfill_rows(root)
            if additions:
                with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=root,
                                                 prefix=".index-", delete=False) as fh:
                    writer = csv.DictWriter(fh, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(existing + additions)
                    fh.flush()
                    os.fsync(fh.fileno())
                    temp = fh.name
                os.replace(temp, root / "index.csv")
    missing = [row["file"] for row in additions if not row["licence_credit"].split("|", 1)[0].strip()]
    return {"root": str(root), "dry_run": dry_run, "added": additions, "missing_licence": missing}

# Unified, local-only asset resolution. Registry metadata is intentionally never inferred from filenames.
import difflib
import hashlib
import re
import shutil
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from functools import lru_cache

from .licence import LICENCE_ALLOW, OWN_LICENCES, norm_licence
from .util import EngineError


@dataclass(frozen=True)
class Resolved:
    ref: str
    path: Path
    tier: str
    kind: str
    licence: str
    source: str
    source_url: str
    author: str
    sha256: str
    bytes: int


_KINDS = {
    'image': {'.jpg', '.jpeg', '.png', '.webp', '.avif', '.tif', '.tiff'},
    'svg': {'.svg'}, 'video': library.VIDEO_EXT, 'model': library.MODEL_EXT,
    'font': {'.ttf', '.otf', '.woff', '.woff2'},
    'music': {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'},
}
_HASHES: dict[tuple[str, int, int], str] = {}
_USED: ContextVar[tuple[Resolved, ...]] = ContextVar("used_assets", default=())


def _hash(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key not in _HASHES:
        digest = hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                digest.update(block)
        _HASHES[key] = digest.hexdigest()
    return _HASHES[key]


@lru_cache(maxsize=128)
def _read_rows(path: Path, mtime: int, size: int) -> list[dict]:
    with path.open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    st = path.stat()
    return _read_rows(path, st.st_mtime_ns, st.st_size)


def _roots(project: Path | None) -> list[tuple[str, Path]]:
    roots = ([('project', Path(project))] if project is not None else [])
    return roots + [('open', config.OPEN_ASSETS), ('vendor', config.VENDOR)] + [
        ('download', root) for root in (*config.STOCK_DIRS, config.DOWNLOAD_DIR)]


def _safe(root: Path, rel: str) -> Path | None:
    if '..' in Path(rel).parts:
        return None
    return (root / rel).absolute()


def _file_kind(path: Path, requested: str | None = None) -> str:
    ext = path.suffix.lower()
    aliases = {'map': {'image', 'svg'}, 'texture': {'image'}, 'flag': {'svg'}, 'icon': {'svg'}, 'sfx': {'music'}}
    actual = next((k for k, exts in _KINDS.items() if ext in exts), '')
    if requested:
        if actual not in aliases.get(requested, {requested}):
            raise EngineError(f"asset {path}: extension does not match kind {requested}")
        return requested
    return actual


def _row_meta(row: dict) -> dict:
    lic, _, author = (row.get('licence_credit') or '').partition('|')
    return {'licence': (row.get('licence') or row.get('license') or lic).strip(),
            'source': (row.get('source') or row.get('source_id') or '').strip(),
            'source_url': (row.get('source_url') or '').strip(),
            'author': (row.get('author') or author).strip()}


def _metadata_for(root: Path, path: Path, tier: str) -> dict:
    rel = path.relative_to(root.absolute()).as_posix()
    meta: dict = {}
    if tier == 'open':
        for row in sorted(_rows(root / 'catalog.csv'), key=lambda r: len(r.get('path', ''))):
            collection = row.get('path', '').strip().strip('/')
            if collection and (rel == collection or rel.startswith(collection + '/')):
                meta = _row_meta(row)
                meta['source_url'] = meta['source_url'] or library._url(meta['source'])
                meta['author'] = row.get('attribution', '') or meta['author']
        if rel.startswith('2d/flags/flag-icons/'):
            meta.update(licence='MIT', source='flag-icons', source_url='https://github.com/lipis/flag-icons',
                        author='lipis/flag-icons contributors')
    if tier in {'open', 'download', 'project'}:
        for row in _rows(root / 'index.csv'):
            if row.get('file', '').strip() == rel:
                meta.update({k: v for k, v in _row_meta(row).items() if v})
                break
        else:
            if tier == 'download':
                return {}
    if tier == 'download':
        side = _metadata(path)
        for key in ('licence', 'source_url', 'author'):
            if not meta.get(key):
                meta[key] = side.get(key, '')
        meta['source'] = meta.get('source') or side.get('source_id', '')
    if tier == 'vendor':
        meta = {'licence': 'Own', 'source': 'vendor', 'source_url': '', 'author': ''}
    return meta


def _allowed(meta: dict, kind: str) -> bool:
    lic = norm_licence(meta.get('licence', ''))
    source = meta.get('source', '').strip().lower()
    if source in {'', 'unknown', 'youtube'}:
        return False
    if kind in {'music', 'sfx'}:
        return 'epidemic' in source and bool(lic) and bool(meta.get('source_url'))
    return (lic in LICENCE_ALLOW | {'mit'} and bool(source)
            and (lic in OWN_LICENCES or bool(re.match(r'^https?://[^\s/]+\.[^\s]+$', meta.get('source_url', '')))))


def candidates(ref: str, *, project: Path | None = None, kind: str | None = None,
               _hash_files: bool = True) -> list[Resolved]:
    prefix, sep, tail = ref.partition(':')
    if not sep or prefix not in {'project', 'open', 'vendor', 'download'}:
        prefix, tail = '', ref
    if Path(tail).is_absolute():
        raise EngineError(f'asset ref must be relative: {ref}')
    found = []
    seen = set()
    for tier, root in _roots(project):
        if prefix and prefix != tier:
            continue
        if tier == 'vendor' and (not Path(tail).parts or Path(tail).parts[0] not in {'fonts', 'textures', 'js'}):
            continue
        path = _safe(root, tail)
        if path is None or not path.is_file() or path.stat().st_size == 0 or path in seen:
            continue
        meta = _metadata_for(root, path, tier)
        if tier == 'download' and not meta:
            continue
        try:
            actual = _file_kind(path, kind)
        except EngineError:
            continue
        if not actual:
            continue
        seen.add(path)
        found.append(Resolved(ref, path, tier, actual, meta.get('licence', ''), meta.get('source', ''),
                              meta.get('source_url', ''), meta.get('author', ''), _hash(path) if _hash_files else '', path.stat().st_size))
    return found


def resolve(ref: str, *, project: Path | None = None, kind: str | None = None,
            require_licence: bool = True, refresh: bool = False, _hash_files: bool = True) -> Resolved:
    if refresh:
        clear_index()
    hits = candidates(ref, project=project, kind=kind, _hash_files=_hash_files)
    if not hits:
        raise EngineError(f'asset not found or not registered: {ref}')
    same = [a for a in hits if a.tier == hits[0].tier]
    if len(same) > 1:
        raise EngineError(f'ambiguous asset {ref}: ' + ', '.join(str(a.path) for a in same))
    asset = same[0]
    meta = asdict(asset)
    if asset.kind in {'music', 'sfx'} and 'epidemic' not in asset.source.lower():
        raise EngineError(f'asset {ref}: audio source must be Epidemic Sound')
    if require_licence and not _allowed(meta, asset.kind):
        row = {'file': ref, 'licence': asset.licence, 'source': asset.source,
               'source_url': asset.source_url, 'author': asset.author}
        raise EngineError('asset metadata invalid; fill registry row: ' + json.dumps(row, ensure_ascii=False))
    return asset


def flag_for(code: str, variant: str = '4x3') -> str:
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z0-9]{2,3})?', code) or variant not in {'4x3', '1x1'}:
        raise EngineError(f'invalid flag code or variant: {code}, {variant}')
    rel = f'2d/flags/flag-icons/flags/{variant}/{code}.svg'
    directory = config.OPEN_ASSETS / '2d/flags/flag-icons/flags' / variant
    if not (directory / f'{code}.svg').is_file():
        suggestions = difflib.get_close_matches(code, [p.stem for p in directory.glob('*.svg')], n=3, cutoff=0.4)
        raise EngineError(f'unknown flag {code}; closest: {", ".join(suggestions) or "none"}')
    return 'open:' + rel


def icon_for(name: str) -> str:
    if not re.fullmatch(r'[a-z0-9_-]+', name):
        raise EngineError(f'invalid icon name: {name}')
    for collection in ('maki', 'temaki'):
        rel = f'2d/map-icons/{collection}/icons/{name}.svg'
        if (config.OPEN_ASSETS / rel).is_file():
            return 'open:' + rel
    raise EngineError(f'unknown map icon: {name}')


def clear_index() -> None:
    _HASHES.clear()
    _read_rows.cache_clear()


def find(query: str, kind: str | None = None, project: Path | None = None, limit: int = 20) -> list[Resolved]:
    tokens = re.findall(r'[\w-]+', query.casefold())
    roots = _roots(project)
    signatures = []
    for tier, root in roots:
        for file in (root / 'index.csv', root / 'catalog.csv', root / 'VERSIONS.json'):
            st = file.stat() if file.is_file() else None
            signatures.append([str(file), st.st_mtime_ns if st else None, st.st_size if st else None])
    cache_key = hashlib.sha256(json.dumps([query, kind, str(project), signatures]).encode()).hexdigest()
    cache_file = config.CACHE_DIR / 'asset_index' / (cache_key + '.json')
    refs = None
    if cache_file.is_file():
        try:
            refs = json.loads(cache_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    if not isinstance(refs, list):
        refs = []
        for tier, root in roots:
            if not root.is_dir():
                continue
            if tier == 'download':
                paths = [root / r['file'] for r in _rows(root / 'index.csv') if r.get('file')]
            elif tier == 'vendor':
                paths = (p for folder in ('fonts', 'textures', 'js') for p in (root / folder).rglob('*'))
            else:
                paths = root.rglob('*')
            for path in paths:
                if path.is_file() and all(t in str(path.relative_to(root)).casefold() for t in tokens):
                    refs.append([tier, str(root), path.relative_to(root).as_posix()])
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        _atomic_text(cache_file, json.dumps(refs))
    hits = []
    for tier, root, rel in refs:
        ref = f'{tier}:{rel}'
        try:
            result = resolve(ref, project=project, kind=kind, _hash_files=False)
        except EngineError:
            continue
        path = Path(root) / rel
        if result.path != path.absolute():
            continue
        rank = sum(t in path.stem.casefold() for t in tokens)
        hits.append(({'project': 0, 'open': 1, 'vendor': 2, 'download': 3}[tier], -rank, str(path), result))
    hits.sort(key=lambda x: x[:3])
    return [replace(hit[3], sha256=_hash(hit[3].path)) for hit in (hits[:limit] if limit > 0 else hits)]


def register_used(resolved: Resolved) -> None:
    used = _USED.get()
    if resolved not in used:
        _USED.set((*used, resolved))


def register(path: Path, *, licence: str, source: str, source_url: str = '', author: str = '',
             dest: str = 'stock', kind: str | None = None, project: Path | None = None,
             asset_id: str | None = None, dry_run: bool = False) -> dict:
    path = Path(path).resolve()
    if not path.is_file() or not path.stat().st_size:
        raise EngineError(f'asset file missing or empty: {path}')
    kind = _file_kind(path, kind)
    if not kind:
        raise EngineError(f'unsupported asset type: {path.suffix}')
    meta = {'licence': licence, 'source': source, 'source_url': source_url, 'author': author}
    if source.strip().lower() in {'', 'unknown', 'youtube'} or not _allowed(meta, kind):
        raise EngineError('invalid licence, source or source URL; audio requires Epidemic Sound')
    if dest not in {'stock', 'open', 'project'} or (dest == 'project' and project is None):
        raise EngineError('destination must be stock, open, or project with --project')
    digest = _hash(path)
    root = {'stock': config.STOCK_DIRS[0], 'open': config.OPEN_ASSETS,
            'project': Path(project) if project else Path('.') }[dest]
    registry = root / 'index.csv'
    aid = asset_id or re.sub(r'[^a-z0-9_]+', '_', path.stem.lower()).strip('_')
    if not aid or not re.fullmatch(r'[a-zA-Z0-9_-]+', aid):
        raise EngineError('invalid asset id')
    subdir = 'assets' if dest == 'project' else kind
    rel = f'{subdir}/{aid}{path.suffix.lower()}'
    target = root / rel
    row = {'file': rel, 'source_id': source, 'source': source, 'source_url': source_url,
           'licence': licence, 'licence_credit': f'{licence} | {author}' if author else licence,
           'author': author, 'kind': kind, 'bytes': str(path.stat().st_size), 'sha256': digest, 'asset_id': aid}

    def check() -> None:
        for _, other in _roots(project):
            for old in _rows(other / 'index.csv'):
                old_path = _safe(other, old.get('file', ''))
                old_hash = old.get('sha256') or (_hash(old_path) if old_path and old_path.is_file() else '')
                if old_hash == digest:
                    raise EngineError(f"duplicate sha256: {old.get('asset_id') or old.get('file')} ({other / old.get('file', '')})")
        if target.exists():
            raise EngineError(f'asset destination already exists: {target}')
        if dest == 'project':
            timeline_path = root / 'master_timeline.json'
            if not timeline_path.is_file():
                raise EngineError(f'project timeline missing: {timeline_path}')
            from . import timeline
            timeline.load(root)

    check()
    result = {'destination': str(target), 'registry': str(registry), 'row': row, 'dry_run': dry_run}
    if dry_run:
        return result
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'index.csv.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        check()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        rows = _rows(registry)
        columns = list(rows[0]) if rows else []
        columns += [key for key in row if key not in columns]
        with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8', dir=root,
                                         prefix='.index-', delete=False) as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows([*rows, row])
            fh.flush()
            os.fsync(fh.fileno())
            temp = fh.name
        os.replace(temp, registry)
        if dest == 'project':
            timeline_path = root / 'master_timeline.json'
            data = json.loads(timeline_path.read_text(encoding='utf-8'))
            data.setdefault('credits_manifest', []).append({'asset_id': aid, 'path': rel, 'source_url': source_url,
                                                             'license': licence, 'author': author or None,
                                                             'watermark_free': True})
            _atomic_text(timeline_path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')
            credits_path = root / 'credits.txt'
            old = credits_path.read_text(encoding='utf-8') if credits_path.exists() else ''
            if source_url and source_url not in old:
                _atomic_text(credits_path, old.rstrip() + '\n\nAset tambahan\n' + source_url + '\n')
    clear_index()
    return result


def _atomic_text(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=f'.{path.name}-', delete=False) as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
        temp = fh.name
    os.replace(temp, path)


def ensure_credits(tl, project: Path) -> tuple[object, list[dict]]:
    """Return a timeline copy and new credits; never edit the source timeline or credits file."""
    from .gates.layer1 import visual_refs
    from .timeline import Credit

    result = tl.model_copy(deep=True)
    new = []
    refs = visual_refs(result)
    for segment in result.segments:
        scene = segment.gsap_scene_config
        if scene and scene.morph:
            refs.append((segment.seg_index, scene.morph.asset_id, scene.morph.asset, "morph"))
        if scene:
            flags = [p.flag for p in scene.pins]
            flags += [f for r in scene.regions or [] for f in [r.flag, *(c.flag for c in r.changes)]]
            flags += [c.flag for c in scene.flag_characters or []]
            for flag in flags:
                if flag:
                    refs.append((segment.seg_index, f'flag_{flag.code}', flag_for(flag.code, flag.variant), 'flag'))
    for _, asset_id, path, role in refs:
        if result.credit_for(ref=asset_id, path=path):
            continue
        if not path and role == 'card image' and asset_id and asset_id.startswith(('project:', 'open:', 'vendor:', 'download:')):
            path = asset_id
        if not path:
            raise EngineError(f'{role} {asset_id!r} has no credited asset path')
        try:
            asset = resolve(path, project=project, kind='music' if role == 'bgm' else None)
        except EngineError:
            if role == 'bgm':   # non-Epidemic music is not auto-credited; gate A02 fails it with a build report
                continue
            raise
        aid = asset_id or re.sub(r'[^a-z0-9_]+', '_', asset.path.stem.lower()).strip('_')
        row = {'asset_id': aid, 'path': path, 'source_url': asset.source_url, 'license': asset.licence,
               'author': asset.author or None, 'watermark_free': True}
        result.credits_manifest.append(Credit.model_validate(row))
        new.append(row)
    for asset in _USED.get():
        if asset.tier == 'vendor' or result.credit_for(path=asset.ref):
            continue
        if not _allowed(asdict(asset), asset.kind):
            raise EngineError('asset metadata invalid; fill registry row: ' + json.dumps(asdict(asset), default=str))
        aid = re.sub(r'[^a-z0-9_]+', '_', asset.path.stem.lower()).strip('_')
        row = {'asset_id': aid, 'path': asset.ref, 'source_url': asset.source_url, 'license': asset.licence,
               'author': asset.author or None, 'watermark_free': True}
        result.credits_manifest.append(Credit.model_validate(row))
        new.append(row)
    _USED.set(())
    return result, new


def resolve_timeline_paths(tl, project: Path):
    """Prepare only new or non-project asset references for existing path-based renderers."""
    from . import props

    result = tl.model_copy(deep=True)
    replacements = {}

    def rewrite(obj, attribute, kind=None):
        ref = getattr(obj, attribute, None)
        if not ref or (not ref.startswith(('project:', 'open:', 'vendor:', 'download:'))
                       and (Path(project) / ref).is_file()):
            return
        asset = resolve(ref, project=project, kind=kind)
        replacements[ref] = str(asset.path)
        setattr(obj, attribute, str(asset.path))

    for segment in result.segments:
        if segment.source_image:
            rewrite(segment, 'source_image')
        if segment.source_video:
            rewrite(segment, 'source_video')
        scene = segment.gsap_scene_config
        if scene:
            rewrite(scene, 'map_asset', 'map')
            if scene.morph:
                rewrite(scene.morph, 'asset', 'map')
            for _, prop, _, _ in props.flat(segment):
                rewrite(prop, 'asset')
            for layer in scene.layers or []:
                rewrite(layer, 'asset')
    if result.audio_chain.bgm_path:
        rewrite(result.audio_chain, 'bgm_path', 'music')
    card_ids = {card.asset_ref for segment in result.segments if segment.gsap_scene_config
                for card in segment.gsap_scene_config.cards if card.asset_ref}
    for credit in result.credits_manifest:
        if credit.path:
            if credit.path in replacements:
                credit.path = replacements[credit.path]
            elif credit.asset_id in card_ids and credit.path.startswith(('project:', 'open:', 'vendor:', 'download:')):
                rewrite(credit, 'path')
    return result
