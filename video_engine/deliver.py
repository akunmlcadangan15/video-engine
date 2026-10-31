"""Delivery plan and local upload package (naming, splitting, Drive layout). The engine uploads nothing; it writes
the tree an operator uploads or opens in Drive.

Build check (check_build, before any plan): the final must come from the last build. Refused when, next to the
final, build_report.failed.json exists, build_report.json is missing or not ok, its build_id differs from
build_manifest.json (a later build started and did not publish), or the final's sha256 differs from the report's
final_sha256.

Naming (NN = settings.project_number, Topik = settings.topic, date = settings.date):
  Drive folder  "NN - YYYY-MM-DD - Topik/05 Final"   final video (+ parts), JOIN.txt, SRT, thumbnail, metadata
                "NN - YYYY-MM-DD - Topik/03 Aset"    credits
  files         "NN - Topik - final.mp4"             when size <= PART_MAX_BYTES
                "NN - Topik - final.mp4.partN"       byte-split parts, N = 1.., each <= PART_MAX_BYTES
                "NN - JOIN.txt"                      join instructions (always written)
                "NN - credits.txt"                   copy of <project>/credits.txt
                "NN - final.srt"                     captions from the pinned timeline words
                "NN - thumbnail.jpg"                 middle frame of the final video
                "NN - metadata.json"                 YouTube Shorts / Reels title, description, tags

`export_package` writes the same tree into a local destination (upload to Drive is not part of the engine).
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .timeline import Timeline
from .util import EngineError

PART_MAX_BYTES = 100 * 1000 * 1000
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@dataclass
class DeliveryPlan:
    drive_root: str
    final_dir: str
    assets_dir: str
    files: list[dict] = field(default_factory=list)   # {"name", "dir", "bytes", "source", "range": [a, b] | None}
    join_txt: str = ""
    dry_run: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", BAD_CHARS.sub("", s)).strip()


def names(tl: Timeline) -> dict:
    S = tl.settings
    missing = [k for k in ("project_number", "topic", "date") if not getattr(S, k)]
    if missing:
        raise EngineError(f"settings.{', settings.'.join(missing)} required for delivery naming")
    nn, topic = S.project_number, clean(S.topic)
    root = f"{nn} - {S.date} - {topic}"
    return {"root": root, "final_dir": f"{root}/05 Final", "assets_dir": f"{root}/03 Aset",
            "final": f"{nn} - {topic} - final.mp4", "credits": f"{nn} - credits.txt", "join": f"{nn} - JOIN.txt",
            "srt": f"{nn} - {topic} - final.srt", "thumbnail": f"{nn} - {topic} - thumbnail.jpg",
            "metadata": f"{nn} - {topic} - metadata.json"}


def split_ranges(size: int, part_max: int = PART_MAX_BYTES) -> list[tuple[int, int]]:
    """Byte ranges [a, b) of the parts; one range when size <= part_max."""
    if size <= 0:
        raise EngineError("final file is empty")
    n = math.ceil(size / part_max)
    step = math.ceil(size / n)
    return [(i * step, min(size, (i + 1) * step)) for i in range(n)]


def join_text(final_name: str, parts: list[str]) -> str:
    if len(parts) <= 1:
        return f"File: {final_name}\nNo join needed (single file).\n"
    q = " ".join(f'"{p}"' for p in parts)
    return (f"File: {final_name} ({len(parts)} parts)\n"
            f"Linux/macOS: cat {q} > \"{final_name}\"\n"
            f"Windows: copy /b {'+'.join(chr(34) + p + chr(34) for p in parts)} \"{final_name}\"\n")


def check_build(final: Path) -> dict:
    """Raise EngineError unless `final` is the published output of the last build; returns its build report."""
    from .manifest import MANIFEST, sha256
    d = Path(final).parent
    if not Path(final).is_file():
        raise EngineError(f"final video not found: {final} (run `engine build` first)")
    if (d / "build_report.failed.json").is_file():
        raise EngineError(f"last build failed ({d / 'build_report.failed.json'}): {Path(final).name} is stale; "
                          f"fix it and rebuild before delivering")
    try:
        rep = json.loads((d / "build_report.json").read_text())
        man = json.loads((d / MANIFEST).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise EngineError(f"no build report/manifest next to {final}: {e}") from None
    if not rep.get("ok") or not rep.get("build_id"):
        raise EngineError("build_report.json is not a successful build with a build_id; rebuild before delivering")
    if rep["build_id"] != man.get("build_id"):
        raise EngineError(f"build_report.json is build {rep['build_id']} but the last build is {man.get('build_id')}: "
                          f"{Path(final).name} is stale; rebuild before delivering")
    if rep.get("final_sha256") != sha256(final, fresh=True):
        raise EngineError(f"{final} differs from the file build {rep['build_id']} published")
    return rep


def _ts(frame: int, fps: int) -> str:
    ms = round(frame * 1000 / fps)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(tl: Timeline) -> str:
    """SRT cues from the pinned caption words: adjacent words join while the gap is <= 2 frames and the line <= 42
    characters (at most 3 words per cue)."""
    fps = tl.settings.fps
    cues: list[dict] = []
    for w in tl.captions:
        text = w.word.strip()
        if not text:
            continue
        last = cues[-1] if cues else None
        if (last and w.start_frame - last["end"] <= 2 and len(last["words"]) < 3
                and len(last["text"]) + 1 + len(text) <= 42):
            last["text"] += " " + text
            last["words"].append(text)
            last["end"] = w.end_frame
        else:
            cues.append({"start": w.start_frame, "end": w.end_frame, "text": text, "words": [text]})
    return "".join(f"{i}\n{_ts(c['start'], fps)} --> {_ts(c['end'], fps)}\n{c['text']}\n\n"
                   for i, c in enumerate(cues, start=1))


def metadata(tl: Timeline) -> dict:
    """YouTube Shorts / Instagram Reels metadata from the timeline: title, description, tags and hashtags."""
    S = tl.settings
    title = clean(S.topic or tl.project_id)[:100]
    script = " ".join(w.word.strip() for w in tl.captions if w.word.strip())
    credit_lines = [f"{c.asset_id}: {c.source_url} ({c.license})" +
                    (f" - {c.author}" if c.author else "") for c in tl.credits_manifest]
    desc = script[:4000]
    if credit_lines:
        desc += ("\n\n" if desc else "") + "Sumber & lisensi:\n" + "\n".join(credit_lines)
    words = re.findall(r"[\w-]{3,}", f"{S.topic or ''} {script}", flags=re.UNICODE)
    seen, tags = set(), []
    for w in words:
        k = w.casefold()
        if k not in seen and len(tags) < 15:
            seen.add(k)
            tags.append(w)
    hashtags = ["#Shorts"] + ["#" + re.sub(r"[^\w]", "", w) for w in tags[:5] if re.sub(r"[^\w]", "", w)]
    reel = title + (("\n\n" + script[:2200]) if script else "") + "\n\n" + " ".join(hashtags)
    return {"platforms": ["youtube-shorts", "instagram-reels"],
            "title": title,
            "description": desc,
            "tags": tags,
            "category": "Education",
            "language": tl.audio_chain.language,
            "made_for_kids": False,
            "youtube": {"title": title, "description": desc, "tags": tags, "shorts": True},
            "reels": {"caption": reel, "hashtags": hashtags}}


def _metadata_text(tl: Timeline) -> str:
    return json.dumps(metadata(tl), indent=2, ensure_ascii=False) + "\n"


def thumbnail(tl: Timeline, final: Path, out: Path) -> Path:
    """Middle frame of `final` as a JPEG thumbnail."""
    from PIL import Image
    from .sheet import frame_rgb
    S = tl.settings
    frame = max(0, tl.total_frames // 2)
    img = Image.frombytes("RGB", (S.width, S.height), frame_rgb(final, frame, S.fps, S.width, S.height))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="JPEG", quality=90)
    return out


def plan(tl: Timeline, project_dir: Path, final: Path, part_max: int = PART_MAX_BYTES) -> DeliveryPlan:
    n = names(tl)
    if not final.is_file():
        raise EngineError(f"final video not found: {final} (run `engine build` first)")
    size = final.stat().st_size
    ranges = split_ranges(size, part_max)
    files = []
    if len(ranges) == 1:
        files.append({"name": n["final"], "dir": n["final_dir"], "bytes": size, "source": str(final), "range": None})
        parts = [n["final"]]
    else:
        parts = [f"{n['final']}.part{i + 1}" for i in range(len(ranges))]
        for p, (a, b) in zip(parts, ranges):
            files.append({"name": p, "dir": n["final_dir"], "bytes": b - a, "source": str(final), "range": [a, b]})
    jt = join_text(n["final"], parts)
    files.append({"name": n["join"], "dir": n["final_dir"], "bytes": len(jt.encode()), "source": "generated", "range": None})
    st = srt(tl)
    files.append({"name": n["srt"], "dir": n["final_dir"], "bytes": len(st.encode()), "source": "generated", "range": None})
    md = _metadata_text(tl)
    files.append({"name": n["metadata"], "dir": n["final_dir"], "bytes": len(md.encode()), "source": "generated", "range": None})
    files.append({"name": n["thumbnail"], "dir": n["final_dir"], "bytes": None, "source": "generated", "range": None})
    credits = project_dir / "credits.txt"
    if not credits.is_file():
        raise EngineError("credits.txt missing in project dir")
    files.append({"name": n["credits"], "dir": n["assets_dir"], "bytes": credits.stat().st_size, "source": str(credits), "range": None})
    return DeliveryPlan(n["root"], n["final_dir"], n["assets_dir"], files, jt, True)


def export_package(tl: Timeline, project_dir: Path, final: Path, out_dir: Path,
                   part_max: int = PART_MAX_BYTES, verify: bool = True) -> dict:
    """Write the upload-ready tree (video parts, JOIN, credits, SRT, thumbnail, metadata) under `out_dir`."""
    if verify:
        check_build(final)
    pl = plan(tl, project_dir, final, part_max)
    n = names(tl)
    root = Path(out_dir)
    written = []
    for f in pl.files:
        if f["name"] == n["thumbnail"]:
            continue
        dst = root / f["dir"] / f["name"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if f["name"] == n["join"]:
            dst.write_text(pl.join_txt, encoding="utf-8")
        elif f["name"] == n["srt"]:
            dst.write_text(srt(tl), encoding="utf-8")
        elif f["name"] == n["metadata"]:
            dst.write_text(_metadata_text(tl), encoding="utf-8")
        elif f["range"]:
            a, b = f["range"]
            with final.open("rb") as src, dst.open("wb") as out:
                src.seek(a)
                left = b - a
                while left:
                    chunk = src.read(min(1 << 20, left))
                    if not chunk:
                        raise EngineError(f"{final} ended before byte {b}")
                    out.write(chunk)
                    left -= len(chunk)
        else:
            shutil.copyfile(f["source"], dst)
        written.append(str(dst.relative_to(root)))
    thumb = thumbnail(tl, final, root / n["final_dir"] / n["thumbnail"])
    written.append(str(thumb.relative_to(root)))
    return {"root": str(root), "drive_root": n["root"], "files": sorted(written),
            "metadata": metadata(tl), "srt_cues": srt(tl).count(" --> ")}


def write_package(*args, **kwargs) -> dict:
    """Deprecated alias kept for callers of the earlier name; use export_package."""
    return export_package(*args, **kwargs)
