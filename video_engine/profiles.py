"""Channel style profiles: the resolver. `profiles/<name>.toml` merged over `profiles/default.toml`.

Layers, lowest first: Pydantic field defaults (video_engine/profile_schema.py) < default.toml < the profile's
`extends` chain (for each profile, its `include` files in list order, then its own file) < the project's
`profile_overrides` (OVERRIDABLE tables) < the project's `caption_style` keys and `audio_chain` loudness. A profile
lists only the keys it changes; tables merge key by key, a list or scalar replaces the lower value. Fallback is per
key: a profile that cannot be read or validated is an EngineError, never a silent return to the default profile.
Global safety limits (true peak, loudness tolerance) may only be tightened.

The table models live in `profile_schema` and are re-exported here (`profiles.Theme`, `profiles.CameraProfile`).
Entry points: `of(timeline)`, `load(name, overrides)`; `origins(name)` says which file set each key."""
from __future__ import annotations

import difflib
import json
import os
import re
import time
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from . import config
from . import units
from .gate_registry import ANTI_SLOP_IDS, KERNEL_GATE_IDS, LINT_GATE_IDS, NEVER_ADVISORY, STYLE_GATE_IDS, TECHNICAL_GATE_IDS  # noqa: F401  (re-exported as profiles.*, docs/GATES.md)
from .profile_schema import *  # noqa: F401,F403  (every table model and constant: profiles.Theme, profiles.REVEAL_EASE, ...)
from .profile_schema import (CAPTION_PY_KEYS, CAPTION_RUST_KEYS, CAPTION_SLOTS, REVEAL_EASE, AntiSlopGate,
                             CameraProfile, FlagsProfile, FormatProfile, FxProfile, MotionProfile, ProfileData,
                             ReferenceStyle, RenderProfile, TransitionsProfile, TypeScale)
from .util import EngineError, blake3_json

if TYPE_CHECKING:
    from .layout import LayoutProfile
    from .grade import Grade
    from .timeline import Timeline
    from .profile_schema import (AudioProfile, DraftProfile, Legibility, PrintStyle, SoundProfile, StyleGates, Theme,
                                 Watermark)


@dataclass(frozen=True)
class Profile:
    name: str
    data: ProfileData
    blake3: str          # hash of the merged (effective) profile

    @property
    def audio(self) -> AudioProfile:
        return self.data.audio

    @property
    def caption_style(self) -> dict:
        return self.data.caption_style

    @property
    def gates(self) -> StyleGates:
        return self.data.gates

    @property
    def draft(self) -> DraftProfile:
        return self.data.draft

    @property
    def theme(self) -> Theme:
        return self.data.theme

    @property
    def type(self) -> TypeScale:
        return self.data.type

    @property
    def watermark(self) -> Watermark | None:
        return self.data.watermark

    @property
    def sound(self) -> SoundProfile | None:
        return self.data.sound

    @property
    def legibility(self) -> Legibility | None:
        return self.data.legibility

    @property
    def layout(self) -> LayoutProfile | None:
        return self.data.layout

    @property
    def print(self) -> PrintStyle | None:
        return self.data.print

    @property
    def camera(self) -> CameraProfile:
        return self.data.camera

    @property
    def motion(self) -> MotionProfile:
        return self.data.motion

    @property
    def grade(self) -> Grade | None:
        return self.data.grade

    @property
    def transitions(self) -> TransitionsProfile:
        return self.data.transitions

    @property
    def render(self) -> RenderProfile:
        return self.data.render

    @property
    def fx(self) -> FxProfile:
        return self.data.fx

    @property
    def reference_style(self) -> ReferenceStyle:
        return self.data.reference_style

    def render_values(self, renderer: str) -> dict:
        """Profile values that reach a segment renderer ("hf" or "native"); a segment cache key includes them."""
        if renderer != "hf":
            return {}
        out = json.loads(self.data.theme.model_dump_json(exclude={"entities", "palette"}))
        if (cp := self.data.camera.scene()) is not None:
            out["camera_pulse"] = cp
        if (cb := self.data.camera.bank()) is not None:
            out["camera_bank"] = cb
        out["motion"] = self.data.motion.render_values()
        return out

    def report(self) -> dict:
        return {"name": self.name, "blake3": self.blake3}


@dataclass(frozen=True)
class Loudness:
    """Effective loudness parameters of one timeline (project audio_chain value, else profile value)."""
    target_lufs: float
    true_peak: float
    lra: float
    tolerance_lu: float


DEFAULT = "default"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
INCLUDE_KEY = "include"     # channel file: other .toml files of the profiles directory, merged below it
EXTENDS_KEY = "extends"     # channel file: another channel profile this one builds on
EXTENDS_MAX = 8             # longest extends chain
LOADER_KEYS = (INCLUDE_KEY, EXTENDS_KEY)   # read by the loader, never part of ProfileData
ENGINE_ORIGIN = "(engine default)"         # origins(): a key no file writes (its Pydantic default)


def merge(base: dict, over: dict) -> dict:
    """Key-by-key merge; nested tables merge recursively, every other value (a list, a scalar, None) replaces the
    base value. The one merge of the engine: profile layers and project overrides (where None turns an optional
    table such as [watermark] off; validation rejects None where it is not allowed)."""
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def path_of(name: str) -> Path:
    return config.PROFILES_DIR / f"{name}.toml"


def names() -> list[str]:
    return sorted(p.stem for p in config.PROFILES_DIR.glob("*.toml"))


# ------------------------------------------------------------------------------------------------- caption style
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_caption_style(cs: dict, orient: str = "portrait", where: str = "") -> None:
    """EngineError for a caption_style table (profile or timeline) with a key no reader knows, a value of the wrong
    type, or geometry outside the reference canvas of `orient`. The single check of the table: profiles, timelines
    and the effective style (profile + timeline) all go through it. `where` prefixes the message."""
    pre = f"{where}: " if where else ""
    ref_h = units.REF[orient][1]
    known = (*CAPTION_PY_KEYS, *CAPTION_RUST_KEYS)
    for k in cs:
        if k not in known:
            close = difflib.get_close_matches(str(k), known, n=1)
            raise EngineError(f"{pre}caption_style.{k}: unknown key" + (f" (did you mean {close[0]}?)" if close else "")
                              + f"; known: {', '.join(sorted(known))}")
    for k, kinds in CAPTION_RUST_KEYS.items():
        if k not in cs:
            continue
        v = cs[k]
        if (isinstance(v, bool) and bool not in kinds) or not isinstance(v, kinds):
            want = "true or false" if kinds == (bool,) else "text" if kinds == (str,) else \
                   "a whole number" if kinds == (int,) else "a number"
            raise EngineError(f"{pre}caption_style.{k} must be {want}, got {v!r}")
    if "slot" in cs and cs["slot"] not in CAPTION_SLOTS:
        raise EngineError(f"{pre}caption_style.slot must be one of {', '.join(CAPTION_SLOTS)}")
    for k in ("auto_place", "forbid_box"):
        if k in cs and not isinstance(cs[k], bool):
            raise EngineError(f"{pre}caption_style.{k} must be true or false")
    ab = cs.get("ab_sizes")
    if ab is not None and (not isinstance(ab, list) or not ab
                           or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in ab)):
        raise EngineError(f"{pre}caption_style.ab_sizes must be a non-empty list of positive integers")
    if cs.get("forbid_box") and cs.get("box_bg") is True:
        raise EngineError(f"{pre}caption_style.forbid_box = true but box_bg = true; captions never use a box")
    c = cs.get("clearance")
    if c is not None and (not _is_num(c) or c < 0):
        raise EngineError(f"{pre}caption_style.clearance must be a number >= 0")
    y = cs.get("fixed_y")
    if y is not None and (not _is_num(y) or not 0 < y < ref_h):
        raise EngineError(f"{pre}caption_style.fixed_y must be a number in 0..{ref_h} (px at {ref_h} height, {orient})")
    b = cs.get("place_band")
    if b is not None and (not isinstance(b, list) or len(b) != 2 or not all(_is_num(v) for v in b)
                          or not 0 < b[0] <= b[1] < 1):
        raise EngineError(f"{pre}caption_style.place_band must be [lo, hi] with 0 < lo <= hi < 1 (fractions of H)")
    m = cs.get("max_bottom_y")
    if m is not None and (not _is_num(m) or not 0 < m <= ref_h):
        raise EngineError(f"{pre}caption_style.max_bottom_y must be a number in 0..{ref_h} "
                          f"(px at {ref_h} height, {orient})")
    yo = cs.get("y_overrides")
    if yo is not None and (not isinstance(yo, list) or not all(
            isinstance(o, (list, tuple)) and len(o) == 3 and all(_is_num(v) for v in o) for o in yo)):
        raise EngineError(f"{pre}caption_style.y_overrides must be a list of [start_frame, end_frame, y_px]")


PORTRAIT_GEOMETRY_CAPTION_KEYS = ("fixed_y", "max_bottom_y", "place_band")
# Approved brand ranges of the [print] positions, px at the reference canvas of each orientation.
PRINT_RANGES = {
    "portrait": {"year_cx": (0, 1080), "year_cy": (520, 600), "heading_baseline_y": (360, 400)},
    "landscape": {"year_cx": (0, 1920), "year_cy": (300, 460), "heading_baseline_y": (120, 200)},
}


def profile_orientation(d: ProfileData) -> str | None:
    """Declared [format] orientation, else "portrait" for a profile with portrait geometry, else None (any)."""
    if d.format.orientation is not None:
        return d.format.orientation
    if d.layout is not None or d.print is not None or any(k in d.caption_style for k in PORTRAIT_GEOMETRY_CAPTION_KEYS):
        return "portrait"
    return None


def _check_print(name: str, d: ProfileData) -> None:
    if d.print is None:
        return
    orient = profile_orientation(d) or "portrait"
    for key, (lo, hi) in PRINT_RANGES[orient].items():
        v = getattr(d.print, key)
        if not lo <= v <= hi:
            raise EngineError(f"profile '{name}': print.{key} = {v:g} outside the approved {orient} range {lo:g}..{hi:g} "
                              f"(px at {units.REF[orient][0]}x{units.REF[orient][1]})")


def check_orientation(prof: "Profile", W: int, H: int) -> None:
    """EngineError when the profile is written for the other orientation than the W x H frame."""
    want = profile_orientation(prof.data)
    have = units.orientation_of(W, H)
    if want is not None and want != have:
        raise EngineError(f"profile '{prof.name}' is a {want} profile but the frame {W}x{H} is {have}; "
                          f"use a {have} profile (for example geoverum_landscape) or change settings.width/height")


def _check_safety(name: str, d: ProfileData, base: ProfileData) -> None:
    if d.audio.true_peak > base.audio.true_peak:
        raise EngineError(f"profile '{name}': audio.true_peak {d.audio.true_peak} is looser than the global limit "
                          f"{base.audio.true_peak} dBTP")
    if d.audio.tolerance_lu > base.audio.tolerance_lu:
        raise EngineError(f"profile '{name}': audio.tolerance_lu {d.audio.tolerance_lu} is looser than the global "
                          f"limit {base.audio.tolerance_lu} LU")


# --------------------------------------------------------------------------------------------- files and layers
def _str_list(table: dict, key: str, where: Path) -> list[str]:
    v = table.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise EngineError(f"profile {where}: `{key}` must be a list of file paths relative to the profiles "
                          f"directory")
    return v


def _include_list(name: str, table: dict, where: Path) -> list[str]:
    """The `include` key of a channel profile: a list of relative .toml paths (none when absent)."""
    return _str_list(table, INCLUDE_KEY, where)


def _profile_file(name: str, rel: str, suffix: str, what: str) -> Path:
    """Path of the profiles-relative file `rel` (an `include` part or a `[draft] rules_file`): it must be relative,
    end in `suffix` and stay inside the profiles directory."""
    base = config.PROFILES_DIR.resolve()
    if Path(rel).is_absolute() or not rel.endswith(suffix):
        raise EngineError(f"profile '{name}': {what} {rel!r} must be a relative path ending in {suffix}")
    path = (base / rel).resolve()
    if base not in path.parents:
        raise EngineError(f"profile '{name}': {what} {rel!r} leaves the profiles directory {base}")
    return path


def _read_file(name: str, rel: str, suffix: str, what: str) -> str:
    path = _profile_file(name, rel, suffix, what)
    try:
        return path.read_text()
    except FileNotFoundError:
        raise EngineError(f"profile '{name}': {what} {rel!r} not found: {path}") from None
    except OSError as e:
        raise EngineError(f"profile {path}: {e}") from e


def _read_include(name: str, rel: str) -> str:
    """Text of the included file `rel` (relative to the profiles directory; it must stay inside it)."""
    return _read_file(name, rel, ".toml", "include")


def _toml(text: str, where) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise EngineError(f"profile {where}: {e}") from e


def _extends_of(name: str, table: dict) -> str | None:
    ext = table.get(EXTENDS_KEY)
    if ext is None:
        return None
    if not isinstance(ext, str) or not NAME_RE.match(ext):
        raise EngineError(f"profile {path_of(name)}: `{EXTENDS_KEY}` must be the name of another profile")
    if ext == DEFAULT:
        raise EngineError(f"profile {path_of(name)}: `{EXTENDS_KEY} = \"{DEFAULT}\"` is not needed, every profile "
                          f"already builds on the default profile")
    return ext


Layer = tuple[str, str, str]   # (kind: default | include | channel, file path, file text)


def _layers(name: str) -> tuple[Layer, ...]:
    """Files of profile `name` in merge order, lowest first: default.toml, then for every profile of the `extends`
    chain (the base first) its `include` files in list order and its own file."""
    def read(n: str, child: str | None) -> str:
        f = path_of(n)
        try:
            return f.read_text()
        except FileNotFoundError:
            known = ", ".join(names()) or "none"
            if child is not None:
                raise EngineError(f"profile '{child}': `{EXTENDS_KEY}` names profile '{n}', which is not found: {f} "
                                  f"(known: {known})") from None
            raise EngineError(f"profile '{n}' not found: {f} (known: {known})") from None
        except OSError as e:
            raise EngineError(f"profile {f}: {e}") from e

    layers: list[Layer] = [("default", str(path_of(DEFAULT)), read(DEFAULT, None))]
    if name == DEFAULT:
        return tuple(layers)
    chain: list[tuple[str, str, list[str]]] = []
    cur, child = name, None
    while cur is not None:
        if cur in [c[0] for c in chain]:
            raise EngineError(f"profile '{name}': `{EXTENDS_KEY}` loops back to '{cur}' "
                              f"({' -> '.join([c[0] for c in chain] + [cur])})")
        if len(chain) >= EXTENDS_MAX:
            raise EngineError(f"profile '{name}': `{EXTENDS_KEY}` chain is longer than {EXTENDS_MAX} profiles")
        text = read(cur, child)
        table = _toml(text, path_of(cur))
        chain.append((cur, text, _include_list(cur, table, path_of(cur))))
        cur, child = _extends_of(cur, table), cur
    for prof, text, includes in reversed(chain):
        layers += [("include", str(_profile_file(prof, rel, ".toml", "include")), _read_include(prof, rel))
                   for rel in includes]
        layers.append(("channel", str(path_of(prof)), text))
    return tuple(layers)


def _tables(name: str, layers: tuple[Layer, ...]) -> list[tuple[str, dict]]:
    """Parsed layers [(file path, table)] in merge order, without the loader keys."""
    out = []
    for kind, where, text in layers:
        table = _toml(text, where)
        if kind == "default" and (INCLUDE_KEY in table or EXTENDS_KEY in table):
            key = INCLUDE_KEY if INCLUDE_KEY in table else EXTENDS_KEY
            raise EngineError(f"profile {where}: the default profile cannot `{key}` other files")
        if kind == "include" and (INCLUDE_KEY in table or EXTENDS_KEY in table):
            rel = Path(where).relative_to(config.PROFILES_DIR.resolve()).as_posix()
            raise EngineError(f"profile '{name}': included file {rel!r} cannot include other files")
        out.append((where, {k: v for k, v in table.items() if k not in LOADER_KEYS}))
    return out


# [type] is the base of the type scale: a size the channel sets there reaches every surface that does not set its
# own. (type key) -> surface keys. A surface table that the profile does not have ([layout], [print]) is left absent.
TYPE_CASCADE = {
    "title_px": (("theme", "title_px"), ("layout", "sizes", "title")),
    "title_px_paper": (("print", "title_px"),),
    "pin_label_px": (("theme", "pin_label_px"), ("layout", "sizes", "pin_label")),
}


def _has(d: dict, path: tuple[str, ...]) -> bool:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return False
        d = d[k]
    return True


def _type_cascade(raw: dict, channel: dict) -> dict:
    """`raw` (all layers merged) with the channel's [type] sizes written to the surfaces the channel left open.
    `channel` = the layers above default.toml merged: only a size a channel file writes cascades, and a surface key a
    channel file writes wins."""
    sizes = channel.get("type") if isinstance(channel.get("type"), dict) else {}
    out = raw
    for key, targets in TYPE_CASCADE.items():
        if key not in sizes:
            continue
        for path in targets:
            if _has(channel, path) or not isinstance(raw.get(path[0]), dict):
                continue
            patch: dict = {path[-1]: sizes[key]}
            for k in reversed(path[:-1]):
                patch = {k: patch}
            out = merge(out, patch)
    return out


@lru_cache(maxsize=32)
def _resolved(name: str, layers: tuple[Layer, ...]) -> tuple[dict, dict]:
    """(effective raw table, default.toml table) of the layers, before validation. Memoised on the file contents:
    the tables are shared between callers and must be read, never changed."""
    tables = _tables(name, layers)
    base = tables[0][1]
    channel: dict = {}
    for _, t in tables[1:]:
        channel = merge(channel, t)
    return _type_cascade(merge(base, channel), channel), base


# ------------------------------------------------------------------------------------------------ creative rules
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


def _rule_lines(name: str, rel: str, text: str) -> list[str]:
    rules = [line[2:].strip() for line in text.splitlines() if line.startswith("- ") and line[2:].strip()]
    if not rules:
        raise EngineError(f"profile '{name}': [draft] rules_file {rel!r} has no `- ` rule lines")
    return rules


def _creative_rules(name: str, rel: str) -> list[str]:
    """Rule lines of the single-source markdown file `rel` (profiles-relative) as written: every `- ` line is one
    rule, `{{table.key}}` placeholders unresolved (the profile resolves them: `load(name).draft.rules`)."""
    return _rule_lines(name, rel, _read_file(name, rel, ".md", "[draft] rules_file"))


def _fill_rules(name: str, rel: str, rules: list[str], values: dict) -> list[str]:
    """Rules with every `{{table.key}}` replaced by that value of the effective profile, so a number quoted in the
    creative direction is the number the engine uses, for every profile that shares the file."""
    def value(m: re.Match) -> str:
        cur = values
        for part in m.group(1).split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise EngineError(f"profile '{name}': [draft] rules_file {rel!r} names {{{{{m.group(1)}}}}}, which is "
                                  f"not a key of the effective profile")
            cur = cur[part]
        if isinstance(cur, bool):
            return "true" if cur else "false"
        if isinstance(cur, float):
            return f"{cur:g}"
        if isinstance(cur, (dict, list)):
            return json.dumps(cur, ensure_ascii=False)
        if cur is None:
            raise EngineError(f"profile '{name}': [draft] rules_file {rel!r} names {{{{{m.group(1)}}}}}, which is "
                              f"not set in this profile")
        return str(cur)
    return [_PLACEHOLDER.sub(value, r) for r in rules]


# -------------------------------------------------------------------------------------------------------- hash
_HASH_DEFAULT_TABLES = (("camera", CameraProfile), ("motion", MotionProfile), ("transitions", TransitionsProfile),
                        ("render", RenderProfile), ("flags", FlagsProfile), ("fx", FxProfile))


def hash_dump(data: ProfileData) -> dict:
    """The table `Profile.blake3` hashes: the effective profile without what must not move a hash. A table or key
    added after profiles existed stays out at its default (the engine look), so adding it changed no profile hash;
    machine settings ([resources]) never count. New fields: prefer `Field(exclude_if=...)` on the model; a rule
    here is for a whole table or a key whose default is not expressible there."""
    dump = json.loads(data.model_dump_json())
    for k in ("print", "layout", "grade"):
        if dump.get(k) is None:
            dump.pop(k, None)             # profiles without the table keep their hash
    if isinstance(dump.get("layout"), dict) and dump["layout"].get("fly") is None:
        dump["layout"].pop("fly", None)   # [layout] without [layout.fly] keeps its hash
    for k, cls in _HASH_DEFAULT_TABLES:
        if dump.get(k) == json.loads(cls().model_dump_json()):
            dump.pop(k)                   # a table at its defaults adds nothing
    if isinstance(dump.get("camera"), dict) and dump["camera"].get("mode") == "planar":
        dump["camera"].pop("mode")        # the planar default camera mode adds nothing
    if isinstance(dump.get("transitions"), dict):   # zoom_through keys at their defaults add nothing
        dflt = json.loads(TransitionsProfile().model_dump_json())
        for k in [k for k in dump["transitions"] if k.startswith("through_") and dump["transitions"][k] == dflt[k]]:
            dump["transitions"].pop(k)
    gates = dump["gates"]
    if gates.get("camera") is None:
        gates.pop("camera", None)
    else:
        for k in ("min_drift_per_s", "step_warn"):     # P01 tables without P02 / a P03 step_warn keep their hash
            if gates["camera"].get(k) is None:
                gates["camera"].pop(k, None)
    for k in ("require", "advisory"):
        if not gates.get(k):
            gates.pop(k, None)            # no required / advisory gates: nothing to hash
    if gates.get("anti_slop") == json.loads(AntiSlopGate().model_dump_json()):
        gates.pop("anti_slop", None)      # default anti-slop levels add nothing
    for k in ("cta", "ending_keyword", "duration_exact", "geo"):
        if gates.get(k) is None:
            gates.pop(k, None)            # absent gate tables add nothing
    if dump["theme"].get("reveal_ease") == list(REVEAL_EASE):
        dump["theme"].pop("reveal_ease")  # the default reveal ease adds nothing
    if isinstance(dump.get("render"), dict) and dump["render"].get("final_bit_depth") is None:
        dump["render"].pop("final_bit_depth", None)
    if dump.get("type") == json.loads(TypeScale().model_dump_json()):
        dump.pop("type", None)
    if not dump.get("framing"):
        dump.pop("framing", None)
    if dump.get("format") == json.loads(FormatProfile().model_dump_json()):
        dump.pop("format", None)
    if isinstance(dump.get("watermark"), dict) and dump["watermark"].get("corner") is None:
        dump["watermark"].pop("corner", None)   # the orientation's default corner adds nothing
    # [reference_style] hashes as written (unset keys add nothing), exactly as when the table was untyped
    dump["reference_style"] = data.reference_style.model_dump(mode="json", exclude_unset=True)
    dump.pop("resources", None)           # the worker budget is a machine setting; stage keys carry the thread counts
    return dump


# ------------------------------------------------------------------------------------------------------- build
@lru_cache(maxsize=32)
def _build(name: str, layers: tuple[Layer, ...], rules_text: str | None) -> Profile:
    """Profile of the layers (`_layers` order). Memoised on the file contents (and the text of the rules file), so
    an edited file is a new profile and an unchanged one is not validated twice."""
    raw, base_raw = _resolved(name, layers)
    try:
        data = ProfileData.model_validate(raw)
        base = data if name == DEFAULT else ProfileData.model_validate(base_raw)
    except ValidationError as e:
        raise EngineError(f"profile '{name}': {e}") from e
    check_caption_style(data.caption_style, profile_orientation(data) or "portrait", f"profile '{name}'")
    _check_print(name, data)
    try:
        from . import framing
        framing.check_overrides(data.framing)
    except ValueError as e:
        raise EngineError(f"profile '{name}': {e}") from None
    _check_safety(name, data, base)
    if data.draft.rules_file:
        rel = data.draft.rules_file
        text = _read_file(name, rel, ".md", "[draft] rules_file") if rules_text is None else rules_text
        data.draft.rules = _fill_rules(name, rel, _rule_lines(name, rel, text),
                                       json.loads(data.model_dump_json(by_alias=True)))
    return Profile(name, data, blake3_json(hash_dump(data)))


def _parse(name: str, texts: tuple[str, ...]) -> Profile:
    """Profile from texts in the order default.toml, the channel file, then the files its `include` lists (for
    callers that hold the texts; the engine loads through `load`)."""
    if name == DEFAULT:
        return _build(name, (("default", str(path_of(DEFAULT)), texts[0]),), None)
    includes = _include_list(name, _toml(texts[1], path_of(name)), path_of(name))
    if len(texts) != 2 + len(includes):
        raise EngineError(f"profile '{name}': {len(includes)} include file(s) listed but {len(texts) - 2} given")
    layers = [("default", str(path_of(DEFAULT)), texts[0])]
    layers += [("include", str(_profile_file(name, rel, ".toml", "include")), texts[2 + i])
               for i, rel in enumerate(includes)]
    layers.append(("channel", str(path_of(name)), texts[1]))
    return _build(name, tuple(layers), None)


OVERRIDABLE = ("theme", "camera", "watermark", "motion", "grade", "render", "flags")   # project profile_overrides tables
_CACHE_MAX = 64
_OVERRIDDEN: dict[tuple[str, str], Profile] = {}          # (base hash, overrides JSON) -> profile, bounded
_LOADED: dict[tuple[str, str], tuple[tuple, Profile]] = {}   # (profiles dir, name) -> (file signature, profile)
_RACY_NS = 2_000_000_000   # a file changed this recently may change again inside one timestamp tick


def _bounded_put(memo: dict, key, value) -> None:
    if len(memo) >= _CACHE_MAX:
        memo.pop(next(iter(memo)))
    memo[key] = value


def clear_cache() -> None:
    """Drop every memoised profile (tests; a caller that swaps profile files and wants no stale hit)."""
    _build.cache_clear()
    _resolved.cache_clear()
    _OVERRIDDEN.clear()
    _LOADED.clear()


def _signature(files: tuple[str, ...]) -> tuple | None:
    """(path, mtime, size, inode) of every file, or None when one is missing or changed so recently that its
    timestamp cannot yet tell two edits apart (then the caller reads the contents)."""
    now, out = time.time_ns(), []
    try:
        for f in files:
            st = os.stat(f)
            if now - st.st_mtime_ns < _RACY_NS:
                return None
            out.append((f, st.st_mtime_ns, st.st_size, st.st_ino))
    except OSError:
        return None
    return tuple(out)


def _override(base: Profile, over: dict) -> Profile:
    """Profile `base` with a project's profile_overrides on top (only OVERRIDABLE tables; gates, audio and caption rules
    stay the channel's). The hash covers the channel hash and the overrides, so caches follow both."""
    bad = sorted(set(over) - set(OVERRIDABLE))
    if bad:
        raise EngineError(f"profile_overrides: {', '.join(bad)} cannot be overridden by a project "
                          f"(allowed: {', '.join(OVERRIDABLE)})")
    key = (base.blake3, json.dumps(over, sort_keys=True))
    if (hit := _OVERRIDDEN.get(key)) is not None:
        return hit
    raw = merge(json.loads(base.data.model_dump_json(by_alias=True)), over)
    # load() resolves rules_file into draft.rules and keeps the path. That pair is one source,
    # already rejected at parse when a profile file itself contains both. Revalidation here only
    # checks the overridden tables, so the path is set aside and restored on the new profile.
    draft = raw.get("draft")
    rules_file = None
    if isinstance(draft, dict) and draft.get("rules") and draft.get("rules_file"):
        rules_file = draft["rules_file"]
        raw = {**raw, "draft": {**draft, "rules_file": None}}
    try:
        data = ProfileData.model_validate(raw)
    except ValidationError as e:
        raise EngineError(f"profile '{base.name}' with profile_overrides: {e}") from e
    if rules_file is not None:
        data.draft.rules_file = rules_file
    prof = Profile(base.name, data, blake3_json({"profile": base.blake3, "overrides": over}))
    _bounded_put(_OVERRIDDEN, key, prof)
    return prof


def load(name: str | None = None, overrides: dict | None = None) -> Profile:
    """The effective profile `name` (None = the default profile), with a project's `overrides` on top. Layers, lowest
    first: Pydantic defaults < default.toml < the `extends` chain (each profile's `include` files, then its file) <
    overrides. A profile that cannot be read or validated is an EngineError: there is no fallback to another
    profile. Repeated calls cost a few `stat` calls while the files are unchanged."""
    if overrides:
        return _override(load(name), overrides)
    name = name or DEFAULT
    if not NAME_RE.match(name):
        raise EngineError(f"profile name {name!r} invalid (lowercase letters, digits, '_' and '-')")
    key = (str(config.PROFILES_DIR), name)
    hit = _LOADED.get(key)
    if hit is not None and hit[0] == _signature(tuple(f for f, *_ in hit[0])):
        return hit[1]
    layers = _layers(name)
    raw, _ = _resolved(name, layers)
    files = [where for _, where, _ in layers]
    rules_text = None
    rel = raw.get("draft", {}).get("rules_file") if isinstance(raw.get("draft"), dict) else None
    if isinstance(rel, str):
        rules_text = _read_file(name, rel, ".md", "[draft] rules_file")
        files.append(str(_profile_file(name, rel, ".md", "[draft] rules_file")))
    prof = _build(name, layers, rules_text)
    sig = _signature(tuple(files))
    if sig is not None:
        _bounded_put(_LOADED, key, (sig, prof))
    return prof


def origins(name: str | None = None) -> dict[str, str]:
    """Where each key of the effective profile `name` comes from: dotted key -> the profiles-relative file that set
    it last, "[type] title_px" style notes for a cascaded size, or ENGINE_ORIGIN for a key no file writes."""
    name = name or DEFAULT
    layers = _layers(name)
    root = config.PROFILES_DIR.resolve()

    def flat(d: dict, pre: str = "") -> dict:
        out = {}
        for k, v in d.items():
            if isinstance(v, dict) and v:
                out.update(flat(v, f"{pre}{k}."))
            else:
                out[f"{pre}{k}"] = v
        return out

    src: dict[str, str] = {}
    for where, table in _tables(name, layers):
        try:
            rel = Path(where).resolve().relative_to(root).as_posix()
        except ValueError:
            rel = where
        for k in flat(table):
            src[k] = rel
    raw, _ = _resolved(name, layers)
    merged_plain: dict = {}
    for _, t in _tables(name, layers):
        merged_plain = merge(merged_plain, t)
    before, after = flat(merged_plain), flat(raw)
    for key, targets in TYPE_CASCADE.items():
        for path in targets:
            k = ".".join(path)
            if after.get(k) != before.get(k):
                src[k] = f"[type] {key} ({src.get('type.' + key, ENGINE_ORIGIN)})"
    effective = flat(json.loads(load(name).data.model_dump_json(by_alias=True)))
    return {k: src.get(k, ENGINE_ORIGIN) for k in sorted(effective)}


# ------------------------------------------------------------------------------------------------------ flatten
_ENTRY = re.compile(r"^([A-Za-z0-9_-]+|\"[^\"]+\")\s*=")
_HEADER = re.compile(r"^\[([^\[\]]+)\]\s*(#.*)?$")


def _sections(text: str) -> list[tuple[str, list[tuple[str | None, list[str]]]]]:
    """TOML text as [(table header or "" for the root, [(key or None for comments and blank lines, lines)])]. An
    entry runs until its brackets close, so a list written over several lines stays one entry."""
    out: list[tuple[str, list[tuple[str | None, list[str]]]]] = [("", [])]
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        h = _HEADER.match(line.strip())
        if h and not line.startswith((" ", "\t")):
            out.append((h.group(1).strip(), []))
            i += 1
            continue
        m = _ENTRY.match(line)
        if not m:
            out[-1][1].append((None, [line]))
            i += 1
            continue
        block, depth, quote = [line], 0, None
        while True:
            for ch in _strip_comment(block[-1]):
                if quote:
                    quote = None if ch == quote else quote
                elif ch in "\"'":
                    quote = ch
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
            if depth <= 0 or i + 1 >= len(lines):
                break
            i += 1
            block.append(lines[i])
        out[-1][1].append((m.group(1), block))
        i += 1
    return out


def _strip_comment(line: str) -> str:
    quote, out = None, []
    for ch in line:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out)


def flatten(name: str) -> str:
    """Profile `name` as one TOML text that loads to the same profile without its `include` / `extends` files: the
    files of every layer above default.toml merged table by table in load order (a key a higher layer repeats takes
    the higher value, as in `load`), comments kept, and `[draft] rules_file` replaced by the rules it resolves to.
    A profile without `include` and `extends` is returned as written. For tools and tests that read or copy a
    profile as text; the engine itself loads profiles through `load`."""
    text = path_of(name).read_text()
    table = _toml(text, path_of(name))
    if not _include_list(name, table, path_of(name)) and _extends_of(name, table) is None:
        return _inline_rules_file(name, text)
    order: list[str] = []
    merged: dict[str, list[tuple[str | None, list[str]]]] = {}
    for kind, _where, layer_text in _layers(name)[1:]:
        for header, entries in _sections(layer_text):
            if header not in merged:
                merged[header] = []
                order.append(header)
            have = merged[header]
            for key, block in entries:
                if header == "" and key in LOADER_KEYS:
                    continue
                if key is not None:
                    have[:] = [(k, b) for k, b in have if k != key]     # the higher layer's value replaces it
                have.append((key, block))
    order.sort(key=lambda h: h != "")       # root keys first: they must precede every table header
    parts = []
    for header in order:
        body = "\n".join(line for _, block in merged[header] for line in block).strip("\n")
        if header == "":
            if body.strip():
                parts.append(body)
        else:
            parts.append(f"[{header}]" + ("\n" + body if body else ""))
    return _inline_rules_file(name, "\n\n".join(parts) + "\n")


def _inline_rules_file(name: str, flat: str) -> str:
    """Flattened text with `[draft] rules_file` replaced by the inline `rules` list it resolves to, so a flattened
    profile is self-contained (tools/tests that copy one TOML to another directory keep working)."""
    raw = tomllib.loads(flat)
    rel = (raw.get("draft") or {}).get("rules_file")
    if not rel:
        return flat
    rules = load(name).draft.rules
    # json.dumps writes TOML basic-string escapes (\uXXXX, \\, \n, \"). re.sub treats a
    # replacement string as a template, so those backslashes are not copied literally
    # (\u from a character such as ≠ is an error; \n, \1 and \g<...> would change the text).
    # A callable replacement inserts the line unchanged, and tomllib then decodes the escapes.
    inline = "rules = [" + ", ".join(json.dumps(r) for r in rules) + "]"
    flat, n = re.subn(r'(?m)^rules_file\s*=.*$', lambda _match: inline, flat, count=1)
    if n != 1:
        raise EngineError(f"profile '{name}': cannot inline [draft] rules_file")
    return flat


QUALITIES = ("delivery", "proxy")


def validate_quality(quality: str, mode: str) -> None:
    if quality not in QUALITIES:
        raise EngineError(f"unknown render quality {quality!r} (known: {', '.join(QUALITIES)})")
    if quality == "proxy" and mode != "draft":
        raise EngineError("proxy quality requires --mode draft")


def for_quality(tl: "Timeline", quality: str) -> "Timeline":
    """Build-local render override; geometry, creative settings and the source timeline remain unchanged."""
    if quality not in QUALITIES:
        raise EngineError(f"unknown render quality {quality!r}")
    if quality == "delivery":
        return tl
    render = RenderProfile(
        hf_quality="draft", hf_crf=28, segment_crf=28, segment_preset="ultrafast", segment_tune="none",
        final_crf=28, final_preset="ultrafast", final_tune="none", final_maxrate_kbps=None,
        final_bit_depth=8, map_jpeg_quality=85,
    )
    local = tl.model_copy(deep=True)
    local.profile_overrides = {**local.profile_overrides, "render": render.model_dump()}
    return local


def aac_bitrate(tl: "Timeline") -> int:
    """AAC bitrate in kbps of the master audio and of the corrective gain passes.

    `[render] aac_bitrate` wins when it is set (including a project `profile_overrides.render` value). Otherwise
    `[audio] aac_bitrate`. The default is 192. Allowed range is 64..320."""
    over = of(tl).render.aac_bitrate
    return int(of(tl).audio.aac_bitrate if over is None else over)


def aac_bitrate_arg(tl: "Timeline") -> str:
    """ffmpeg `-b:a` value for `aac_bitrate`."""
    return f"{aac_bitrate(tl)}k"


def quality_report(tl: "Timeline") -> dict:
    """Resolved settings actually passed to capture and encode, not nullable profile defaults."""
    from . import hyperframes, maps
    r = of(tl).render
    q, crf, segment = hyperframes.encode_settings(tl)
    return {"width": tl.settings.width, "height": tl.settings.height, "fps": tl.settings.fps,
            "hf_quality": q, "hf_crf": crf, "segment_x264": segment, "final_x264": r.x264("final"),
            "final_pix_fmt": r.final_pix_fmt(), "map_jpeg_quality": r.map_jpeg_quality or maps.MAP_JPEG_QUALITY,
            "aac_bitrate": aac_bitrate(tl)}


def of(tl: "Timeline") -> Profile:
    return load(tl.profile, tl.profile_overrides)


def caption_style(tl: "Timeline") -> dict:
    """Effective caption style: profile caption_style with the project's caption_style keys on top. A profile that
    sets forbid_box rejects box_bg = true from either the profile or a timeline override."""
    st = {**of(tl).caption_style, **tl.caption_style}
    if st.get("forbid_box") and st.get("box_bg") is True:
        raise EngineError("caption_style.forbid_box = true but box_bg = true (timeline override); captions never use "
                          "a box in this channel")
    return st


def check_timeline_caption_style(tl: "Timeline", prof: Profile) -> None:
    """EngineError when the timeline's caption_style keys, alone or over the profile's, are not a valid style for
    the timeline's frame (the Timeline validator calls this; one check for profile and project keys)."""
    orient = units.orientation_of(tl.settings.width, tl.settings.height)
    check_caption_style(tl.caption_style, orient)
    check_caption_style({**prof.caption_style, **tl.caption_style}, orient)


def loudness(tl: "Timeline") -> Loudness:
    """Effective loudness targets: the project's audio_chain values over the profile's. A project may tighten the
    global true-peak safety limit (the default profile's audio.true_peak, as _check_safety holds every profile to)
    but never loosen it."""
    ac, a = tl.audio_chain, of(tl).audio
    if ac.true_peak is not None and ac.true_peak > (limit := load().audio.true_peak):
        raise EngineError(f"audio_chain.true_peak {ac.true_peak} is looser than the global limit {limit} dBTP")
    pick = lambda v, d: d if v is None else v   # noqa: E731
    return Loudness(pick(ac.target_lufs, a.target_lufs), pick(ac.true_peak, a.true_peak), pick(ac.lra, a.lra),
                    a.tolerance_lu)
