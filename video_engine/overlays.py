"""Native overlays: text cards, labels, lower-thirds and printed date headings drawn on native segments.

Each drawn overlay is rendered by Pillow to an RGBA PNG exactly the size of its `box` and composited by ffmpeg
(`overlay` with `enable` on the frame range, alpha fades) in the same encode that produces the segment. Text is
wrapped and shrunk until it fits the box; text that does not fit at MIN_FONT_PX is an error (gate O01).
Headings (profile [print]) are not shrunk: Cinzel caps at print.heading_px with the rules and diamonds of the map
scene heading, centred in the box, baseline HEADING_PAD above the box bottom; they fade over print.heading_fade_s."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config
from .timeline import Overlay, Segment, Timeline
from .util import EngineError, blake3_files

OVERLAY_VERSION = "ov-1"
FONT_BOLD, FONT_SEMI = "Inter-Bold.otf", "Inter-SemiBold.otf"
MIN_FONT_PX = 26                 # at 1920 px output height; scaled with height
MAX_FONT_PX = {"label": 54, "text_card": 72, "lower_third": 64}
SUB_RATIO = 0.62                 # subtitle size / title size
PAD = {"label": (22, 14), "text_card": (40, 32), "lower_third": (36, 22)}   # (x, y) padding at 1920 height
ACCENT = (242, 177, 52, 255)
BG = {"label": (12, 16, 22, 196), "text_card": (14, 18, 24, 214), "lower_third": (10, 12, 16, 200)}
FADE_S = 0.20
SAFE_X, SAFE_TOP, SAFE_BOTTOM = 60, 100, 100   # same safe area as R63
HEADING_PAD = 16                 # px at 1920: box bottom to the heading baseline (room for the shadow)
HEADING_BASE = (360, 400)        # px at 1920: allowed heading baseline y (brand profile)
HEADING_SS = 4                   # supersampling of the heading PNG (1.5 px rules)
FONT_FACE = {"label": "Inter", "text_card": "Inter", "lower_third": "Inter", "heading": "Cinzel"}   # gate T01


@dataclass(frozen=True)
class Layout:
    title_px: int
    title_lines: tuple[str, ...]
    sub_px: int
    sub_lines: tuple[str, ...]
    text_h: int


@lru_cache(maxsize=64)
def _font(name: str, px: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(config.FONTS_DIR / name), px)


def _wrap(text: str, font, max_w: float) -> list[str] | None:
    """Greedy word wrap; None if a single word is wider than max_w."""
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if font.getlength(t) <= max_w:
            cur = t
            continue
        if not cur or font.getlength(w) > max_w:
            return None
        lines.append(cur)
        cur = w
    if cur:
        lines.append(cur)
    return lines


def _scale(H: int) -> float:
    return H / 1920.0


def _print(pr):
    if pr is None:
        raise EngineError("overlay kind 'heading' needs a profile with a [print] section")
    return pr


@lru_cache(maxsize=16)
def _cinzel(px: int, weight: int):
    from PIL import ImageFont
    f = ImageFont.truetype(str(config.FONTS_DIR / "Cinzel[wght].ttf"), px)
    f.set_variation_by_axes([weight])
    return f


def heading_layout(ov: Overlay, pr, H: int = 1920, ss: int = 1) -> dict:
    """Heading geometry in box px x ss: the per-letter x of the caps, the baseline, the rule y, both rules
    (x0, x1) and both diamond centres; `w` = painted width (text + gaps + diamonds + rules)."""
    k = _scale(H) * ss
    px = round(pr.heading_px * k)
    ft = _cinzel(px, pr.heading_weight)
    T = ov.text.strip().upper()
    trk = pr.heading_tracking * px
    xs, x = [], 0.0
    for ch in T:
        xs.append(x)
        x += ft.getlength(ch) + trk
    tw = x - trk
    g, d, ln = pr.rule_gap * k, pr.diamond_px * k, pr.rule_len * k
    w = tw + 2 * (g + d + g + ln)
    bw, bh = ov.box[2] * ss, ov.box[3] * ss
    base = bh - HEADING_PAD * k
    asc = -ft.getbbox("H", anchor="ls")[1]
    tx = (bw - tw) / 2
    rule_y = base - asc / 2
    left = (tx - g - d - g - ln, tx - g - d - g)
    right = (tx + tw + g + d + g, tx + tw + g + d + g + ln)
    return {"px": px, "font": ft, "text": T, "xs": [tx + v for v in xs], "base": base, "asc": asc, "rule_y": rule_y,
            "rules": (left, right), "diamonds": (tx - g - d / 2, tx + tw + g + d / 2), "d": d, "w": w, "tw": tw}


def fit(ov: Overlay, H: int = 1920, pr=None) -> Layout:
    """Largest font size (<= MAX_FONT_PX[kind]) whose wrapped title + subtitle fit inside the padded box.
    Raises EngineError when even MIN_FONT_PX does not fit. Headings keep print.heading_px: their painted width
    and cap height must fit the box."""
    k = _scale(H)
    if ov.kind == "heading":
        hl = heading_layout(ov, _print(pr), H)
        if hl["w"] > ov.box[2] + 0.5 or hl["base"] - hl["asc"] < 0:
            raise EngineError(f"overlay '{ov.id}': heading ({hl['w']:.0f} px wide with its rules) does not fit box "
                              f"{ov.box}; widen the box or shorten the text")
        return Layout(hl["px"], (hl["text"],), 0, (), round(hl["asc"]))
    x, y, w, h = ov.box
    px_pad, py_pad = (v * k for v in PAD[ov.kind])
    accent = 10 * k if ov.kind in ("text_card", "lower_third") else 0
    inner_w, inner_h = w - 2 * px_pad - accent, h - 2 * py_pad
    lo = max(8, round(MIN_FONT_PX * k))
    for px in range(round(MAX_FONT_PX[ov.kind] * k), lo - 1, -2):
        ft = _font(FONT_BOLD if ov.kind != "label" else FONT_SEMI, px)
        tl = _wrap(ov.text.strip(), ft, inner_w)
        if tl is None:
            continue
        sp = max(lo, round(px * SUB_RATIO))
        sl: list[str] = []
        if ov.subtitle:
            sl = _wrap(ov.subtitle.strip(), _font(FONT_SEMI, sp), inner_w)
            if sl is None:
                continue
        th = round(len(tl) * px * 1.18 + (len(sl) * sp * 1.22 + px * 0.25 if sl else 0))
        if th <= inner_h:
            return Layout(px, tuple(tl), sp, tuple(sl), th)
    raise EngineError(f"overlay '{ov.id}': text does not fit box {ov.box} at {lo} px; enlarge the box or shorten the text")


def _hex(c: str, a: float = 1.0) -> tuple:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) + (round(255 * a),)


def render_heading(ov: Overlay, out: Path, pr, H: int = 1920) -> Path:
    """Heading PNG: caps, rules and diamonds in one ink, drawn at HEADING_SS x and downsampled; on a photo light ink
    over a soft shadow (0, 2 px, blur 6 px, print.heading_shadow_alpha black)."""
    from PIL import Image, ImageDraw, ImageFilter
    fit(ov, H, pr)
    ss = HEADING_SS
    hl = heading_layout(ov, pr, H, ss)
    w, h = round(ov.box[2]), round(ov.box[3])
    mask = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(mask)
    for ch, x in zip(hl["text"], hl["xs"]):
        d.text((x, hl["base"]), ch, font=hl["font"], fill=255, anchor="ls")
    rp = pr.rule_px * _scale(H) * ss
    for x0, x1 in hl["rules"]:
        d.rectangle([x0, hl["rule_y"] - rp / 2, x1, hl["rule_y"] + rp / 2], fill=255)
    r = hl["d"] / 2 ** 0.5                          # half-diagonal of a d x d square turned 45 deg
    for cx in hl["diamonds"]:
        cy = hl["rule_y"]
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    k = _scale(H)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if ov.on == "photo":
        sh = mask.filter(ImageFilter.GaussianBlur(6 * k / 2))
        sh = sh.point(lambda v: round(v * pr.heading_shadow_alpha))
        im.paste((0, 0, 0, 255), (0, round(2 * k)), sh)
        layer = Image.new("RGBA", (w, h), _hex(pr.heading_light))
    else:
        layer = Image.new("RGBA", (w, h), _hex(pr.heading_ink))
    layer.putalpha(mask)
    im = Image.alpha_composite(im, layer)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out)
        return out
    return im


def render_im(ov: Overlay, H: int = 1920, pr=None):
    from PIL import Image, ImageDraw
    if ov.kind == "heading":
        return render_heading(ov, None, _print(pr), H)
    k = _scale(H)
    lay = fit(ov, H)
    w, h = round(ov.box[2]), round(ov.box[3])
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = round(18 * k) if ov.kind != "lower_third" else round(6 * k)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=BG[ov.kind])
    px_pad, py_pad = (v * k for v in PAD[ov.kind])
    x0 = px_pad
    if ov.kind in ("text_card", "lower_third"):
        d.rectangle([0, 0, round(10 * k) - 1, h - 1], fill=ACCENT)
        x0 += 10 * k
    ty = py_pad + (h - 2 * py_pad - lay.text_h) / 2     # centre the text block vertically
    ft = _font(FONT_BOLD if ov.kind != "label" else FONT_SEMI, lay.title_px)
    for ln in lay.title_lines:
        lw = ft.getlength(ln)
        tx = (w - lw) / 2 if ov.kind == "label" else x0
        d.text((tx, ty), ln, font=ft, fill=(255, 255, 255, 255))
        ty += lay.title_px * 1.18
    if lay.sub_lines:
        ty += lay.title_px * 0.25
        fs = _font(FONT_SEMI, lay.sub_px)
        for ln in lay.sub_lines:
            sx = (w - fs.getlength(ln)) / 2 if ov.kind == "label" else x0
            d.text((sx, ty), ln, font=fs, fill=(214, 220, 228, 255))
            ty += lay.sub_px * 1.22
    return im


def render_png(ov: Overlay, out: Path, H: int = 1920, pr=None) -> Path:
    from PIL import Image, ImageDraw
    if ov.kind == "heading":
        return render_heading(ov, out, _print(pr), H)
    k = _scale(H)
    lay = fit(ov, H)
    w, h = round(ov.box[2]), round(ov.box[3])
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = round(18 * k) if ov.kind != "lower_third" else round(6 * k)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=BG[ov.kind])
    px_pad, py_pad = (v * k for v in PAD[ov.kind])
    x0 = px_pad
    if ov.kind in ("text_card", "lower_third"):
        d.rectangle([0, 0, round(10 * k) - 1, h - 1], fill=ACCENT)
        x0 += 10 * k
    ty = py_pad + (h - 2 * py_pad - lay.text_h) / 2     # centre the text block vertically
    ft = _font(FONT_BOLD if ov.kind != "label" else FONT_SEMI, lay.title_px)
    for ln in lay.title_lines:
        lw = ft.getlength(ln)
        tx = (w - lw) / 2 if ov.kind == "label" else x0
        d.text((tx, ty), ln, font=ft, fill=(255, 255, 255, 255))
        ty += lay.title_px * 1.18
    if lay.sub_lines:
        ty += lay.title_px * 0.25
        fs = _font(FONT_SEMI, lay.sub_px)
        for ln in lay.sub_lines:
            sx = (w - fs.getlength(ln)) / 2 if ov.kind == "label" else x0
            d.text((sx, ty), ln, font=fs, fill=(214, 220, 228, 255))
            ty += lay.sub_px * 1.22
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def drawn(seg: Segment) -> list[Overlay]:
    return [o for o in seg.overlays if o.drawn]


def frame_range(ov: Overlay, seg: Segment) -> tuple[int, int]:
    a = ov.start_frame or 0
    b = min(ov.end_frame or seg.n_frames, seg.n_frames)
    return a, b


def key_part(seg: Segment, pr=None) -> dict | None:
    """Cache-key contribution of the drawn overlays (None when nothing is drawn)."""
    ds = drawn(seg)
    if not ds:
        return None
    import PIL
    out = {"v": OVERLAY_VERSION, "pillow": PIL.__version__, "ov": [o.model_dump(exclude=None if o.on else {"on"}) for o in ds],
           "fonts": blake3_files([config.FONTS_DIR / FONT_BOLD, config.FONTS_DIR / FONT_SEMI])}
    if any(o.kind == "heading" for o in ds):
        out["heading"] = {"print": _print(pr).model_dump(mode="json"), "pad": HEADING_PAD, "ss": HEADING_SS,
                          "font": blake3_files([config.FONTS_DIR / "Cinzel[wght].ttf"])}
    return out


def _fade_s(ov: Overlay, pr, motion) -> float:
    return pr.heading_fade_s if ov.kind == "heading" else motion.overlay_fade_s


def ffmpeg_parts(seg: Segment, tl: Timeline, png_dir: Path, first_input: int) -> tuple[list[str], str]:
    """Extra ffmpeg inputs (one looped PNG per drawn overlay) and a filter_complex fragment that takes [vbase] and
    yields [vout]. Fades: FADE_S in/out (shortened for short ranges)."""
    from . import profiles
    prof = profiles.of(tl)
    pr = prof.print
    motion = prof.motion
    fps = tl.settings.fps
    dur = seg.n_frames / fps
    args: list[str] = []
    chain: list[str] = []
    cur = "vbase"
    for i, ov in enumerate(drawn(seg)):
        png = render_png(ov, png_dir / f"seg{seg.seg_index:03d}_ov{i}.png", tl.settings.height, pr)
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.6f}", "-i", str(png)]
        a, b = frame_range(ov, seg)
        fd = min(_fade_s(ov, pr, motion), (b - a) / fps / 3)
        ta, tb = a / fps, b / fps
        idx = first_input + i
        chain.append(f"[{idx}:v]format=rgba,fade=t=in:st={ta:.4f}:d={fd:.4f}:alpha=1,"
                     f"fade=t=out:st={tb - fd:.4f}:d={fd:.4f}:alpha=1[o{i}]")
        nxt = f"v{i}"
        chain.append(f"[{cur}][o{i}]overlay=x={round(ov.box[0])}:y={round(ov.box[1])}:"
                     f"enable='between(n,{a},{b - 1})':eof_action=pass:format=auto[{nxt}]")
        cur = nxt
    chain.append(f"[{cur}]scale=out_color_matrix=bt709:out_range=tv:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p[vout]")
    return args, ";".join(chain)


def composite_rgb(frame: bytes, W: int, H: int, seg: Segment, frame_idx: int, scale: float = 1.0, pr=None) -> bytes:
    """Draw the overlays visible at `frame_idx` onto an RGB24 frame (previews and gate snapshots)."""
    ds = [o for o in drawn(seg) if frame_range(o, seg)[0] <= frame_idx < frame_range(o, seg)[1]]
    if not ds:
        return frame
    from PIL import Image
    base = Image.frombytes("RGB", (W, H), frame).convert("RGBA")
    for o in ds:
        ov = render_im(o, round(H / scale), pr)
        if scale != 1.0:
            ov = ov.resize((max(1, round(ov.width * scale)), max(1, round(ov.height * scale))))
        base.alpha_composite(ov, (round(o.box[0] * scale), round(o.box[1] * scale)))
    return base.convert("RGB").tobytes()


def check(seg: Segment, tl: Timeline) -> list[str]:
    """O01 problems of one segment (empty = ok): fit, safe area, frame range, overlap of drawn overlays in time."""
    from . import profiles
    pr = profiles.of(tl).print
    W, H = tl.settings.width, tl.settings.height
    k = _scale(H)
    kx, ky = W / 1080.0, H / 1920.0
    errs = []
    ds = drawn(seg)
    for o in ds:
        x, y, w, h = o.box
        if (x < SAFE_X * kx - 0.5 or x + w > W - SAFE_X * kx + 0.5
                or y < SAFE_TOP * ky - 0.5 or y + h > H - SAFE_BOTTOM * ky + 0.5):
            errs.append(f"overlay '{o.id}' box {o.box} leaves the safe area ({SAFE_X} px sides, {SAFE_TOP}/{SAFE_BOTTOM} px top/bottom)")
        a, b = frame_range(o, seg)
        end = o.end_frame if o.end_frame is not None else seg.n_frames
        if (o.start_frame or 0) >= seg.n_frames or end > seg.n_frames or b <= a:
            errs.append(f"overlay '{o.id}' frame range {o.start_frame}..{o.end_frame} is outside the segment (length {seg.n_frames})")
        try:
            fit(o, H, pr)
        except EngineError as e:
            errs.append(str(e))
            continue
        if o.kind == "heading":
            base = y + h - HEADING_PAD * k
            if not HEADING_BASE[0] * k - 0.5 <= base <= HEADING_BASE[1] * k + 0.5:
                errs.append(f"heading '{o.id}' baseline y {base:.0f} outside {HEADING_BASE[0]}-{HEADING_BASE[1]} "
                            f"(box y + h - {HEADING_PAD})")
            if abs(x + w / 2 - W / 2) > 1.0:
                errs.append(f"heading '{o.id}' box centre x {x + w / 2:.0f} is not the frame centre {W / 2:.0f}")
    for i, p in enumerate(ds):
        for q in ds[i + 1:]:
            (pa, pb), (qa, qb) = frame_range(p, seg), frame_range(q, seg)
            if pa < qb and qa < pb and _boxes_overlap(p.box, q.box):
                errs.append(f"overlays '{p.id}' and '{q.id}' overlap on screen while both are visible")
    if ds and seg.engine_type != "NATIVE_RUST_FFMPEG":
        errs.append("drawn overlays are only supported on native segments")
    return errs


def _boxes_overlap(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def typefaces(seg: Segment, max_n: int) -> list[str]:
    """T01 problems of a native segment: more than max_n typefaces among the drawn overlays visible together."""
    ds = drawn(seg)
    out = []
    for o in ds:
        a, b = frame_range(o, seg)
        faces = sorted({FONT_FACE[q.kind] for q in ds if frame_range(q, seg)[0] < b and a < frame_range(q, seg)[1]})
        m = f"{len(faces)} typefaces ({', '.join(faces)}) while overlay '{o.id}' shows (max {max_n})"
        if len(faces) > max_n and m not in out:
            out.append(m)
    return out
