"""`engine cache --prune`: delete cache entries that no current project references.

Referenced keys are recomputed from each project's master_timeline.json (default: every project under examples/ if present):
words (voiceover transcription), segments (native and HyperFrames, and the graded file of a graded segment whose
rendered file is cached), maps (downscaled map copies, deep tiles and the measured effective width of each
map source), models (model prop sprites), dom (DOM probe
results and screenshots named <segment key>.<probe key>[.<file>]: kept while their HyperFrames segment key is
referenced; "adhoc." entries of probes without a segment are never referenced). Only the entry kinds words/,
segments/, maps/, models/, dom/ directly under the cache root are scanned for unreferenced entries. The sound
cache (sound/) is pruned of broken entries by video_engine.sound.cache.prune (paths reported as sound/...). Other
directories are left alone. Files leased by a running build (video_engine.cache leases) are kept and listed in
"skipped_leased"; lease files of dead holders are reclaimed ("reclaimed_leases"). The scan and the deletions run
under the cache root's exclusive lock.
"""
from __future__ import annotations

from pathlib import Path

from . import audio, cache, config, globe, grade, hyperframes, maps, native, props, timeline
from .sound import cache as sound_cache
from .timeline import NATIVE

PRUNE_KINDS = ("words", "segments", "maps", "models", "dom")   # the only entry kinds this prune scans
DOM_REF = "dom:"   # marker in the referenced set: DOM probe entries whose name starts with this segment key are kept


def referenced(projects: list[Path], root: Path | None = None) -> tuple[set[str], list[str]]:
    """(referenced entry file names, problems). A project that fails to load is reported, and blocks deletion."""
    root = Path(root or config.CACHE_DIR)
    names, problems = set(), []
    for p in projects:
        try:
            tl = timeline.load(p)
            ac = tl.audio_chain
            if ac.voiceover_path and (p / ac.voiceover_path).is_file():
                names.add(audio.words_key(p / ac.voiceover_path, ac.language, ac.stt.model, ac.stt.keyterms) + ".json")
            for s in tl.segments:
                if s.engine_type == NATIVE:
                    sk = native.plan(s, tl, p).key
                    names.add(sk + ".mp4")
                else:
                    sk, mk = hyperframes.keys(s, tl, p)
                    names |= {sk + ".mp4", mk + ".jpg"}
                    names.add(DOM_REF + sk)
                    panels = (hyperframes.split_panels(s, tl)
                              if s.gsap_scene_config and s.gsap_scene_config.split is not None else [(s, tl)])
                    for ps, pt in panels:
                        psk, pmk = hyperframes.keys(ps, pt, p)
                        names |= {psk + ".mp4", pmk + ".jpg", DOM_REF + psk}
                        scene = hyperframes.scene_data(ps, pt, p)
                        names |= {Path(e["img"]).name for e in scene.get("deep", [])}
                        if (sc := ps.gsap_scene_config) and sc.map_asset and (en := maps.effective_cache_name(p / sc.map_asset)):
                            names.add(en)
                        if m := scene.get("morph"):
                            names |= {maps.map_key(p / m["asset"], maps._need_w(scene), maps._q(scene)) + ".jpg",
                                      *(Path(e["img"]).name for e in m["deep"])}
                        if "globe" in scene:
                            names |= globe.cache_names(ps, pt, p, scene["globe"])
                        names |= props.model_cache_names(ps, p, pt.settings.fps)
                rendered = root / "segments" / (sk + ".mp4")
                if rendered.is_file() and (gk := grade.key(s, tl, p, rendered)):
                    names.add(gk + ".mp4")
        except Exception as e:  # noqa: BLE001  (any unreadable project must stop the prune)
            problems.append(f"{p}: {type(e).__name__}: {e}")
    return names, problems


def is_referenced(f: Path, keep: set[str]) -> bool:
    """A cache entry file is referenced: its name is in `keep`, or it is a DOM probe entry (dom/<segment key>.<probe
    key>[.<screenshot>]) of a referenced segment key."""
    return f.name in keep or (f.parent.name == "dom" and DOM_REF + f.name.split(".", 1)[0] in keep)


def prune(projects: list[Path], cache_dir: Path | None = None, dry_run: bool = False) -> dict:
    root = Path(cache_dir or config.CACHE_DIR)
    keep, problems = referenced(projects, root)
    with cache.locked(root, exclusive=True):
        entries = [f for k in PRUNE_KINDS if (root / k).is_dir() for f in sorted((root / k).iterdir())
                   if f.is_file() and not f.name.endswith(".tmp")]
        if problems:
            return {"deleted": [], "kept": len(entries), "problems": problems, "bytes": 0, "skipped_leased": [],
                    "reclaimed_leases": []}
        held, reclaimed = cache.leased(root)
        unref = [f for f in entries if not is_referenced(f, keep)]
        skipped = [f for f in unref if str(f.relative_to(root)) in held]
        stale = [f for f in unref if str(f.relative_to(root)) not in held]
        size = 0
        for f in stale:
            try:
                size += f.stat().st_size
            except FileNotFoundError:
                continue
            if not dry_run:
                f.unlink(missing_ok=True)
        snd = sound_cache.prune(dry_run=dry_run, cache_dir=root)
    key = "deleted" if not dry_run else "would_delete"
    return {key: [str(f.relative_to(root)) for f in stale] + [f"sound/{p}" for p in snd[key]],
            "kept": len(entries) - len(stale), "bytes": size + snd["bytes"], "problems": [],
            "sound_rewritten": [f"sound/{p}" for p in snd["rewritten"]],
            "skipped_leased": [str(f.relative_to(root)) for f in skipped] + [f"sound/{p}" for p in snd["skipped_leased"]],
            "reclaimed_leases": sorted(set(reclaimed) | set(snd["reclaimed_leases"]))}


def default_projects() -> list[Path]:
    ex = config.ENGINE_ROOT / "examples"
    return sorted(d for d in ex.iterdir() if (d / "master_timeline.json").is_file()) if ex.is_dir() else []
