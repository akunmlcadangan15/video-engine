"""Anti-AI-slop rules. AS01 decorative panels, AS02 copy tics, AS03 motion tics, AS04 readable on-screen time,
AS05 composition source and built-HTML tells, AS06 palette tell hues. Taste rules default to warn; every rule is
configurable in profile `[gates.anti_slop]` and per rule in `[gates.anti_slop.levels]`. AS04 is a technical-class rule:
a profile may raise it to fail without adding it to `[gates] require`."""
from __future__ import annotations

import colorsys
import re
from functools import lru_cache
from pathlib import Path

from .. import config, overlays, profiles
from ..timeline import HYPERFRAMES, CaptionWord, Timeline
from .core import Finding

SOURCE_SUFFIXES = {".html", ".htm", ".css", ".jsx", ".tsx", ".vue", ".svelte"}
SOURCE_SKIP = {"assets", "out", "vendor", "node_modules", ".git", ".cache", "drafts", "tmp"}
SOURCE_MAX_BYTES = 1_000_000


def _level(g: profiles.AntiSlopGate, rule: str) -> str:
    return g.level_for(rule)


def _all_text(tl: Timeline, words: list[CaptionWord] | None) -> str:
    from . import style
    texts = style.screen_text(tl, 0, tl.total_frames)
    texts += [w.word for w in words or []]
    return " ".join(t for t in texts if t)


def _findings(tl: Timeline, gate: str, level: str, message: str, seg: int | None = None) -> Finding:
    return Finding(gate, level, message, seg)


# ------------------------------------------------------------------------------------------------ AS01 panels

def as01_panel(tl: Timeline, g: profiles.AntiSlopGate) -> list[Finding]:
    """Native lower thirds and text cards are a dark plate with a left accent strip: the confirmed side-strip panel."""
    out = []
    for s in tl.segments:
        for o in overlays.drawn(s):
            if o.kind in ("lower_third", "text_card"):
                out.append(_findings(tl, "AS01", _level(g, "AS01"),
                                     f"segment {s.seg_index} overlay '{o.id}' kind {o.kind} draws a side accent "
                                     "strip on a dark panel; use a plain panel", s.seg_index))
    return out


# ------------------------------------------------------------------------------------------------ AS02 copy

def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def as02_copy(tl: Timeline, words: list[CaptionWord] | None, g: profiles.AntiSlopGate) -> list[Finding]:
    text = _all_text(tl, words)
    out, lv = [], _level(g, "AS02")
    n_dash = text.count("\u2014")
    per_500 = n_dash / max(1.0, len(text) / 500)
    if n_dash >= g.em_dash_max or (n_dash >= 2 and per_500 > g.em_dash_per_500):
        out.append(_findings(tl, "AS02", lv, f"copy uses {n_dash} em dash(es) in {len(text)} characters"))
    tokens = set(_words(text))
    hits = [b for b in g.buzzwords if b.lower() in tokens]
    if hits:
        out.append(_findings(tl, "AS02", lv, f"marketing buzzword(s) on screen: {', '.join(hits)}"))
    aphoristic = len(re.findall(r"(?:[.!?])\s+No\b", text, flags=re.IGNORECASE))
    if aphoristic >= g.aphoristic_min:
        out.append(_findings(tl, "AS02", lv,
                             f"{aphoristic} aphoristic 'No ...' endings; use a plain statement"))
    if "theater" in tokens:
        out.append(_findings(tl, "AS02", lv, "copy dismisses something as 'theater'"))
    return out


# ------------------------------------------------------------------------------------------------ AS03 motion

EASE_IN_NAMES = {"ease-in", "power1.in", "power2.in", "power3.in", "power4.in", "sine.in", "expo.in", "circ.in",
                 "back.in"}


def _is_ease_in(name: str) -> bool:
    n = name.strip().lower()
    return n in EASE_IN_NAMES or (n.endswith(".in") and not n.endswith(".inout"))


def as03_motion(tl: Timeline, g: profiles.AntiSlopGate) -> list[Finding]:
    """Profile/scene motion that reads as decoration: ease-in entrances, scale near 0, bounce figure entrances."""
    pr = profiles.of(tl)
    out, lv = [], _level(g, "AS03")
    m = pr.motion
    if _is_ease_in(m.entrance):
        out.append(_findings(tl, "AS03", lv, f"motion.entrance '{m.entrance}' is an ease-in; entrances use ease-out"))
    if m.entrance_scale < 0.9:
        out.append(_findings(tl, "AS03", lv,
                             f"motion.entrance_scale {m.entrance_scale:g} is near zero; start at 0.95-0.97"))
    if pr.layout is not None and pr.layout.fly is not None and _is_ease_in(pr.layout.fly.ease_in):
        out.append(_findings(tl, "AS03", lv,
                             f"layout.fly.ease_in '{pr.layout.fly.ease_in}' is an ease-in; entrances use ease-out"))
    for s in tl.segments:
        if s.engine_type != HYPERFRAMES or s.gsap_scene_config is None:
            continue
        for f in s.gsap_scene_config.figures or []:
            if f.motion == "bounce":
                out.append(_findings(tl, "AS03", lv,
                                     f"segment {s.seg_index} figure uses a bounce entrance; use an ease-out counter",
                                     s.seg_index))
    return out


# ------------------------------------------------------------------------------------------------ AS04 read time

def as04_read_time(tl: Timeline, g: profiles.AntiSlopGate) -> list[Finding]:
    """Drawn overlay text must stay on screen long enough to read: max(min_read_s, characters / chars_per_s)."""
    fps, out, lv = tl.settings.fps, [], _level(g, "AS04")
    for s in tl.segments:
        for o in overlays.drawn(s):
            a, b = overlays.frame_range(o, s)
            dur = (b - a) / fps
            chars = len(" ".join(filter(None, [o.text, o.subtitle])).strip())
            need = max(g.min_read_s, chars / g.chars_per_s)
            if dur + 1e-9 < need:
                out.append(_findings(tl, "AS04", lv,
                                     f"segment {s.seg_index} overlay '{o.id}' reads {chars} characters in "
                                     f"{dur:.2f} s (< {need:.2f} s)", s.seg_index))
    return out


# ------------------------------------------------------------------------------------------------ project sources

def _source_files(project_dir: Path) -> list[Path]:
    out = []
    for p in sorted(Path(project_dir).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            if any(part in SOURCE_SKIP for part in p.relative_to(project_dir).parts):
                continue
        except ValueError:
            continue
        try:
            if p.stat().st_size <= SOURCE_MAX_BYTES:
                out.append(p)
        except OSError:
            continue
    return out


def _source_entries(project_dir: Path) -> list[tuple[Path, str]]:
    return [(p, str(p.relative_to(project_dir))) for p in _source_files(project_dir)]


def _built_entries(built_dirs: list[Path] | Path | str) -> list[tuple[Path, str]]:
    if isinstance(built_dirs, (str, Path)):
        built_dirs = [built_dirs]
    out, seen = [], set()
    for d in built_dirs:
        d = Path(d)
        if d.is_file():
            p = d
            where = f"{d.parent.name}/{d.name}"
        else:
            p = d / "index.html"
            where = f"{d.name}/index.html"
        if not p.is_file() or p.suffix.lower() not in (".html", ".htm"):
            continue
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append((p, where))
    return out


ENGINE_HTML_ALLOW = (
    re.compile(r"\bMO\.[A-Za-z_]\w*"),
    re.compile(r"\bwhip\b", re.IGNORECASE),
    re.compile(r"<feGaussianBlur\b"),
    re.compile(r"filter\s*:\s*['\"]?url\(#mb\)"),
    re.compile(r"\blower[-_]?third\b", re.IGNORECASE),
)


@lru_cache(maxsize=1)
def _engine_template_lines() -> frozenset[str]:
    try:
        return frozenset(line.strip() for line in (config.TEMPLATE_DIR / "index.html").read_text().splitlines())
    except OSError:
        return frozenset()


def _engine_generated(line: str) -> bool:
    return line.strip() in _engine_template_lines() or any(rx.search(line) for rx in ENGINE_HTML_ALLOW)


def _lines(entries: list[tuple[Path, str]]):
    for p, where in entries:
        try:
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if _engine_generated(line):
                    continue
                yield where, n, line
        except OSError:
            continue


SOURCE_TELLS = (
    ("gradient text", re.compile(r"background-clip\s*:\s*text|-webkit-text-fill-color\s*:\s*transparent", re.IGNORECASE)),
    ("decorative glass panel", re.compile(r"(?:-webkit-)?backdrop-filter\s*:", re.IGNORECASE)),
    ("repeating stripe/grid decoration", re.compile(r"repeating-linear-gradient", re.IGNORECASE)),
    ("zero-offset glow", re.compile(r"(?:box-shadow|text-shadow|filter)\s*:[^;]*?\b0(?:px)?\s+0(?:px)?\s+\d+px", re.IGNORECASE)),
    ("kicker/eyebrow label", re.compile(r"\b(?:kicker|eyebrow)\b", re.IGNORECASE)),
)
SIDE_STRIP = re.compile(r"border-(?:left|right)\s*:\s*(\d+(?:\.\d+)?)px", re.IGNORECASE)
CLIP_PATH = re.compile(r"clip-path\s*:\s*([^;{}]+)", re.IGNORECASE)


def _as05(entries: list[tuple[Path, str]], g: profiles.AntiSlopGate) -> list[Finding]:
    out, lv, seen = [], _level(g, "AS05"), set()
    for path, n, line in _lines(entries):
        where = f"{path}:{n}"
        for name, rx in SOURCE_TELLS:
            if rx.search(line) and (where, name) not in seen:
                seen.add((where, name))
                out.append(_findings(tl=None, gate="AS05", level=lv, message=f"{where} {name}"))
        m = SIDE_STRIP.search(line)
        if m and float(m.group(1)) > g.side_strip_px and (where, "side accent strip") not in seen:
            seen.add((where, "side accent strip"))
            out.append(_findings(tl=None, gate="AS05", level=lv, message=f"{where} side accent strip"))
        m = CLIP_PATH.search(line)
        if m:
            body = m.group(1).strip()
            verts = body.count(",") + 1 if body.lower().startswith("polygon(") else 0
            if ((body.lower().startswith("path(") or verts >= g.clip_vertices)
                    and (where, "organic clip-path") not in seen):
                seen.add((where, "organic clip-path"))
                out.append(_findings(tl=None, gate="AS05", level=lv, message=f"{where} organic clip-path"))
    return out


def as05_source_tells(project_dir: Path, g: profiles.AntiSlopGate) -> list[Finding]:
    return _as05(_source_entries(Path(project_dir)), g)


def as05_built_html(built_dirs: list[Path] | Path | str, g: profiles.AntiSlopGate) -> list[Finding]:
    return _as05(_built_entries(built_dirs), g)


def _allowed_colours(pr: profiles.Profile) -> set[str]:
    d = pr.data
    vals = set(d.theme.palette) | set(d.theme.entities.values())
    vals |= {d.theme.pin_colour, d.theme.route_colour, d.theme.text_outline_colour, d.theme.block_edge_colour,
             d.theme.label_box_colour, d.theme.year_box_ink}
    if d.theme.year_box_colour:
        vals.add(d.theme.year_box_colour)
    if d.legibility:
        lg = d.legibility
        vals |= {lg.text_fallback, lg.shadow_colour, lg.paper_colour, lg.route_casing_colour, *lg.never_text}
    if d.print:
        vals |= {d.print.ink, d.print.emboss_colour, d.print.year_dark_colour, d.print.heading_ink, d.print.heading_light,
                 d.print.note_ink, d.print.note_light}
    if d.watermark:
        vals.add(d.watermark.colour)
    return {v.lower() for v in vals}


def _hue_of(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, sat, light


HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def _as06(entries: list[tuple[Path, str]], tl: Timeline, g: profiles.AntiSlopGate) -> list[Finding]:
    allowed = _allowed_colours(profiles.of(tl))
    out, lv, seen = [], _level(g, "AS06"), set()
    for path, n, line in _lines(entries):
        where = f"{path}:{n}"
        for raw in HEX.findall(line):
            c = raw.lower()
            if c in allowed:
                continue
            c6 = c if len(c) == 7 else "#" + "".join(ch * 2 for ch in c[1:])
            hue, sat, light = _hue_of(c6)
            if sat < 0.3 or not 0.15 <= light <= 0.85:
                continue
            for lo, hi in g.forbidden_hues:
                if lo <= hue <= hi and (where, c6) not in seen:
                    seen.add((where, c6))
                    out.append(_findings(tl=None, gate="AS06", level=lv,
                                         message=f"{where} colour {c6} hue {hue:.0f} is an AI-tell range "
                                                 f"({lo}-{hi}); use a brand token"))
                    break
    return out


def as06_palette(project_dir: Path, tl: Timeline, g: profiles.AntiSlopGate) -> list[Finding]:
    return _as06(_source_entries(Path(project_dir)), tl, g)


def as06_built_html(built_dirs: list[Path] | Path | str, tl: Timeline, g: profiles.AntiSlopGate) -> list[Finding]:
    return _as06(_built_entries(built_dirs), tl, g)


def run(tl: Timeline, project_dir: Path, words: list[CaptionWord] | None, g: profiles.AntiSlopGate,
        built_dirs: list[Path] | None = None) -> list[Finding]:
    out: list[Finding] = []
    if g.panel:
        out += as01_panel(tl, g)
    if g.copy_tics:
        out += as02_copy(tl, words, g)
    if g.motion:
        out += as03_motion(tl, g)
    if g.read_time:
        out += as04_read_time(tl, g)
    if g.source_tells:
        out += as05_source_tells(Path(project_dir), g)
        out += as05_built_html(built_dirs or [], g)
    if g.palette:
        out += as06_palette(Path(project_dir), tl, g)
        out += as06_built_html(built_dirs or [], tl, g)
    return out
