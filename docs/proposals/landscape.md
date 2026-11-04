# Design: landscape 16:9 output (1080p, 1440p, 4K)

Status: design only, nothing built. Written against the working tree of 2026-11-02.
Related: [proposals/formats-and-fps.md](formats-and-fps.md) (option 2 "a horizontal format" is the seed of this document; when this is built, fold that option into the reference docs and delete the proposal text).

**What this document answers**

- Which code, profile values, JS and docs assume portrait or the 1080x1920 reference.
- The target model: one reference canvas per orientation and one uniform scale `k`.
- Every site to change (file:line), gates affected, 4K encoding and performance, a test and rollout plan.
- What the owner must decide.

Conventions: "confirmed" = read in the source; "uncertain" = inferred or not run. Nothing here was rendered: `vendor/` (Node, Chrome, HyperFrames) is not installed in the audit environment, so every statement about HyperFrames CLI behaviour is uncertain until `hyperframes render --help` and a real 4K render are run (phase 0).

---

## 1. Goals and non-goals

### Goals

1. Render landscape 16:9 at 1920x1080, 2560x1440 and 3840x2160 for normal YouTube videos. Shorts stay portrait (1080x1920, and 2160x3840).
2. Right-sized text, safe areas, caption placement, overlay slots, watermark and effects in landscape. Today a landscape frame gets non-uniform scaling (`kx = 1920/1080 = 1.78`, `ky = 1080/1920 = 0.5625`, ratio 3.16) at every site that does `W/1080`, `H/1920`.
3. No change for existing portrait projects: same profile hash, same segment cache keys, same bytes of `final.mp4` for `examples/minimal` and Geoverum projects at 1080x1920 (and the same numbers at 720x1280 / 2160x3840 where the sizes are 9:16 exactly). Existing caches stay valid.
4. One place that says "what is the reference canvas and the scale" (`video_engine/units.py`), used by Python, passed into HyperFrames scenes for JS, and mirrored in Rust call arguments.
5. A landscape channel profile whose values are expressed in landscape reference px (1920x1080), not as portrait numbers divided by something.

### Non-goals

- Fractional frame rates, HDR, 10-bit, H.265/AV1 (see the fps proposal). 60 fps is discussed only where it changes the x264 level.
- Square or 4:5 output as a first-class format (handled by an explicit rule in 2.1, not designed further).
- Automatic reframing of portrait projects into landscape (a landscape project is authored as landscape).
- Re-tuning Geoverum's brand look. The landscape Geoverum profile is a starting point for the owner to approve.
- A new renderer. The same native (Rust + ffmpeg) and HyperFrames paths are used.

---

## 2. Proposed model

### 2.1 Reference canvas and scale

```
# video_engine/units.py  (new, no dependency on profiles)
PORTRAIT_REF  = (1080, 1920)
LANDSCAPE_REF = (1920, 1080)

def ref_canvas(settings) -> tuple[int, int]:   # (ref_w, ref_h)
    return LANDSCAPE_REF if settings.width > settings.height else PORTRAIT_REF

@dataclass(frozen=True)
class Units:
    W: int; H: int; ref_w: int; ref_h: int
    kx = W / ref_w            # evaluated exactly as today (W / 1080) for portrait
    ky = H / ref_h
    k  = W / ref_w            # the uniform scale; k == ky when the frame is exactly 16:9 or 9:16
    def px(self, v)  -> float # length at the reference canvas -> output px (v * k)
    def x(self, v) / y(self, v)  # position at the reference canvas -> output px (v * kx / v * ky)
    def box(self, b) -> tuple # [x0,y0,x1,y1] at the reference canvas -> output px
    def safe(self) -> SafeArea  # one definition of the R63 safe area in output px (see 2.3)
def units(tl_or_settings) -> Units
```

Rules:

- **Orientation** is derived from `settings.width > settings.height` (landscape), else portrait. No new `settings` field, so existing timelines validate unchanged (`Settings` is `extra="forbid"`, `timeline.py:36-44`, and adding a field would change the timeline hash of every project that writes it).
- **Lengths** (font sizes, outlines, clearances, gaps, stroke widths) scale by the uniform `k`. **Positions** (zone boxes, slot boxes, `fixed_y`) scale by `kx` / `ky`. For an exact 9:16 or 16:9 frame the three are equal.
- A frame whose aspect differs from the reference by more than 1 % (for example 1080x1350, or 1920x800) keeps today's behaviour for portrait (`kx`, `ky` as now, so nothing changes) and gets a new advisory finding `U01` "frame aspect differs from the reference canvas; layout values are scaled non-uniformly". Square frames count as portrait (reference 1080x1920) and raise `U01`. Open question Q3.
- Portrait numerics stay bit-identical because `Units` evaluates `W / 1080` and `H / 1920` with the same operands as the current code. A parametrised test (section 6) pins `Units(1080,1920,...)`, `(720,1280)` and `(2160,3840)` against the legacy expressions.
- Short-side scaling is already used by some effects: `fx.py:61,89` use `min(w, h) / 1080`, which equals `k` in both orientations (portrait short side is W, landscape short side is H). That shows the intended landscape unit: **`k = short side / 1080`**, which equals `W/1080` portrait and `H/1080` landscape. `Units.k` is defined as `W / ref_w`, identical for exact 16:9 and 9:16.
- `r56_min_px` (`layer2.py:35-36`) scales by the diagonal ratio `hypot(W,H)/hypot(1080,1920)`; the diagonal of 1920x1080 equals that of 1080x1920, so it is already orientation-neutral and unchanged (the 100 px threshold means 100 px at a 1080p-class diagonal in both orientations).

### 2.2 Per-orientation profile values

Values come in three kinds:

| Kind | Examples | Rule |
|---|---|---|
| Orientation-neutral lengths | outline widths, glows, route widths, leader lengths | stay as "px at the reference canvas"; `k` does the rest. The doc strings "px at 1920 height" / "at 1080 wide" become "px at the reference canvas (1080x1920 portrait, 1920x1080 landscape)". |
| Portrait-geometry values | caption `fixed_y` 1382, `max_bottom_y` 1480, `place_band` [0.60,0.76], `[layout.zones]`, `[layout.grid]`, `OVERLAY_SLOTS`, `CARD_TOP`, `TITLE_TOP`, `print.year_cx/year_cy/heading_baseline_y`, `year_cx` cap 1080 | must be given per orientation. |
| Brand constants with approved ranges | `print.title_px` 84..96, `label_px` 52..56, `year_cy` 520..600 | ranges are validated at `profiles.py:657-703`; landscape needs its own approved values or ranges. |

**Recommended mechanism (two steps):**

1. **Step A, separate profile files, no loader change.** `profiles/geoverum_landscape.toml` with `include = ["geoverum/entities.toml", "geoverum/legibility.toml", "geoverum/print.toml", "geoverum/sound.toml", "geoverum/landscape/layout.toml"]` (the include mechanism exists: `profiles.py:1036-1060, 1063-1091`; an included file cannot include others, merge order default < includes < channel file). It repeats `[audio]`, `[gates]`, `[watermark]`, `[caption_style]` with landscape numbers. Pros: pure data, cache keys and `engine gates --profile` work unchanged, portrait profile hash untouched by construction. Cons: duplication of channel identity (audio targets, gate lists) that can drift; `print.toml` has portrait absolutes (`year_cx`, `year_cy`, `heading_baseline_y`) so it needs a landscape variant (`geoverum/landscape/print.toml`), and the validator ranges (`profiles.py:657-703`) must be relaxed first.
2. **Step B, orientation overlay inside one profile (end state).** A profile may carry a `[landscape]` table, a partial profile with the same schema, deep-merged (existing `merge`, `profiles.py` `_parse`) over the portrait values when the timeline is landscape. `profiles.load(name, orientation="portrait")` becomes the cache key (`@lru_cache` at `profiles.py:1063` already keys on file texts; add orientation). `profiles.of(tl)` (`profiles.py:1175`, 58 call sites) passes `units.orientation(tl.settings)` and needs no call-site change. Six direct `profiles.load(name)` calls (`timeline.py:1136`, `draft.py:211,262,285,286,435`, `cli.py:344`) get an explicit orientation (draft takes it from `--width/--height`, `cli.py:674`).
   The resolved landscape profile has its own `blake3` in `build_manifest.json`. The portrait dump drops `landscape` so its hash does not change (follow the existing "pop if default" pattern at `profiles.py:1093-1130`).

A profile also declares `[format] orientation = "portrait" | "landscape"` (default portrait, popped from the hash when default). `Timeline` validation (`timeline.py:1136` area) raises `EngineError` when the profile orientation and the settings orientation disagree, so a portrait profile (with `fixed_y = 1382` etc.) can never silently render a landscape frame. A profile without `[format]` is accepted for either orientation only if it has none of the portrait-geometry keys (the `default` profile qualifies once its `clearance` and `fixed_y` handling is orientation-aware).

### 2.3 Landscape reference values (proposed, owner to approve)

All px at 1920x1080. These are proposals, not measured against a design.

| Item | Portrait (now) | Landscape (proposed) | Source of portrait value |
|---|---|---|---|
| Safe area | 60 px sides, 100 px top/bottom | 96 px sides, 54 px top/bottom (5 % of each side, the common title-safe convention) | `layer2.py:24-26`, `overlays.py:27`, `watermark.py:14` |
| Caption size | 84 | 56..60 (7.8 % of H is too large; portrait is 4.4 %) | `ass.rs:51`, `layer2.py:275` |
| Caption `margin_x` | 90 | 160 | `ass.rs:66`, `layer2.py:276` |
| Caption slot / `fixed_y` | slot lower 0.70 H; geoverum `fixed_y` 1382 (0.72 H) | `fixed_y` about 900 (0.83 H), `place_band` [0.74, 0.88] | `ass.rs:177`, `geoverum.toml:30,31,34` |
| `max_bottom_y` (Z01) | 1480, YouTube Shorts UI | none (no Shorts UI in landscape); keep the gate, absent value = no limit | `geoverum.toml:31`, `docs/SPEC.md:150` |
| `clearance` | 160 | 90 | `default.toml:18` |
| Watermark | top right inside safe area, `px = 40` | bottom right (corner option, new `corner` key), `px = 28` | `watermark.py:35-50`, `geoverum.toml:180-` |
| Overlay slots | `OVERLAY_SLOTS` 4 boxes at 1080x1920 | per-orientation table, e.g. text_card top `[96,96,760,180]`, lower_third bottom `[96,820,760,140]`, label top `[96,120,520,80]` | `draft.py:42-47` |
| Layout zones (Geoverum) | safe `[48,288,888,1248]`, z1 `[64,304,888,440]`, z2 `[64,488,888,1100]` | safe `[96,54,1824,1026]`, z1 `[96,72,1100,200]` (title band at top left), z2 `[96,216,1824,990]` | `geoverum/layout.toml:12-17` |
| Grid | 6 columns x 104, gutter 40, x0 64 | 12 columns x 104, gutter 40 gives x0 = 96, right edge 1824 (12*104 + 11*40 = 1688, so x0 = 116 for centring; Q5) | `layout.toml:5-10` |
| Print `year_cx/cy`, `heading_baseline_y` | 540 / 560 / 384 | 960 / 380 / 150 | `profiles.py:670-671,684` |
| `CARD_TOP`, `TITLE_TOP` | 0.12 / 0.77 / 0.11 of H | 0.10 / 0.70 / 0.10 (the cards must fit z1/z2) | `hyperframes.py:80-81` |
| Gates duration | 45..90 s | owner (long-form, minutes) | `geoverum.toml:176-178` |

---

## 3. Affected code sites

Legend: **Ref** = uses the 1080/1920 reference literally; **Port** = portrait-only geometry; **OK** = already orientation-neutral; **Twin** = JS/Rust copy that must change with its Python partner.

### 3.1 New module and helpers

| Site | Change |
|---|---|
| new `video_engine/units.py` | `Units`, `ref_canvas`, `orientation`, `safe_area` (one definition of SAFE_X/TOP/BOTTOM per orientation, replacing the four copies below), `ref_size_str` for messages. |
| `video_engine/timeline.py:36-44` `Settings` | none. Optional: accept `--format landscape-4k` presets only in the CLI (`importer.py:176`, `cli.py:674`), not in the schema. |

### 3.2 Python

| File:line | Assumption | Proposed change |
|---|---|---|
| `layout.py:10` | docstring "px at 1080 x 1920" | reference canvas |
| `layout.py:487-492` `k()`, `z()` | `W/1080, H/1920` | `units(tl).kx, .ky` |
| `layout.py:672,701` | `self.kx, self.ky = W/1080, H/1920`; `z()` | same, via `Units` |
| `layout.py:766-776, 1011-1428` (about 55 `* self.kx` / `* self.ky` uses) | sizes use `ky`, widths use `kx` (e.g. `L.sizes.title * self.ky` :1037, `leader_gap * self.ky` :766, `card_gap * self.kx` :1064) | keep as is; in landscape `kx == ky == k` so no per-site edit. Rule for new code: lengths use `k`. Replace only the two definition lines. |
| `layout.py` planner logic (Z1 title above Z2 map; `top = ceil(z1[1]/(unit*ky))*unit*ky` :1035; `inner = z2 +- pad` :776; hero/caption-ground L09) | **Port**: assumes a vertical stack and captions below the map (`caption_ground`) | uncertain whether data-only zones suffice; phase 4 spike. Planner reads `zones.z1/z2` generally, but title placement "left-aligned on the first column edge" (docs/SPEC.md "Zones") and `caption_ground` (`layout_gates.py:198`) assume the caption band is under Z2. |
| `gates/layout_gates.py:33` `_k`, `:36-37` `_z`, `:49,89,138,164,183` default `W=1080, H=1920`, `:50-52,95,100,141-142,159,185` `* kx`/`* ky`, `:230` `route_pin_gap * p.W / 1080` | **Ref** | take `Units`; drop the `W=1080,H=1920` defaults (tests keep passing explicit values). `:230` becomes `* units.k`. |
| `overlays.py:20,23,28,29` `MIN_FONT_PX=26`, `PAD`, `HEADING_PAD`, `HEADING_BASE=(360,400)` | **Ref/Port**: "at 1920 px output height"; heading baseline y allowed 360..400 is a portrait position | `MIN_FONT_PX` stays 26 px at the reference canvas, scaled by `k` (landscape 1080p: 26 px; today `H/1920` would wrongly give 14.6 px; see Q6 on whether the minimum should differ per orientation). `HEADING_BASE` per orientation from the profile. |
| `overlays.py:27` `SAFE_X,SAFE_TOP,SAFE_BOTTOM = 60,100,100` | **Ref** | `units.safe_area` |
| `overlays.py:66-67` `_scale(H) = H/1920` | **Ref** | `units(...).k` |
| `overlays.py:84,110,147,185,218` `H: int = 1920` defaults | **Ref** | default via `Units`; keep explicit H parameter for the Pillow renderer |
| `overlays.py:330-339` safe-area check `kx, ky = W/1080.0, H/1920.0` | **Ref** | `Units.safe()` |
| `overlays.py:354-355` heading centred on frame centre x | OK |  |
| `watermark.py:14,35,49-50` `SAFE_X,SAFE_TOP`, `px*H/1920`, `W - SAFE_X*W/1080`, `SAFE_TOP*H/1920` | **Ref/Port**: top-right anchor | `Units`; new `Watermark.corner` ("top_right" default, "bottom_right"); `px` scaled by `k` (not `H/1920`: at 1080p landscape `H/1920` = 0.56, wrong). |
| `captions.py:13-15,38` `fixed_y * H / 1920` | **Ref** (a `y`, position) | `units.y(fixed_y)`; docs say "px at the reference canvas height". |
| `captions.py:35-46` `rust_style` passes `size`, `margin_x`, `outline`, `shadow*` raw | **uncertain / existing gap**: nothing scales caption `size` (84), `margin_x` (90), `outline` (5), `shadow_blur` by the output size. `caption_pad` (`layer2.py:431`) does scale the reach by `H/1920` while `size` is raw, so the two disagree at any size other than 1080x1920. Verify on `2160x3840` in phase 0. | scale these keys by `k` inside `rust_style()` (identity at k=1, so portrait 1080x1920 unchanged) and have R62/Z01 read the same scaled values from one helper (`captions.effective_style(tl)`). If the 4K portrait case is intentionally unscaled today, tell the owner (Q7). |
| `gates/layer2.py:20,36` `R56_MIN_PX_1080P`, `r56_min_px` | OK (diagonal) | none; rename the constant comment to "1080p-class diagonal" |
| `layer2.py:24-26` `SAFE_X,SAFE_TOP,SAFE_BOTTOM` | **Ref** | `units.safe_area`; keep module constants as the portrait defaults for imports in tests |
| `layer2.py:27,75-76,275-285` R62 caption margins: `size` default 84, `margin_x` default 90, safe top/bottom `SAFE_TOP*H/1920` (:277) | **Ref/Port** | read the effective (scaled) style; safe from `Units` |
| `layer2.py:28,431` `CAPTION_REACH_BAND = 24.0`, `* H / 1920` | **Ref** | `* k` |
| `layer2.py:29` `PLACE_STEP = 10` px | **Ref** (output px search step) | `max(2, round(10 * k))`? At 4K a 10 px step is fine but slow; keep 10, document it as output px. |
| `layer2.py:441` `max_bottom` `* H/1920` | **Ref** (position) | `units.y` |
| `layer2.py:336,534,586,735-738` element-box safe area, `clearance * H / 1920`, place_captions top/bottom, `watermark_clear_px * H/1920` | **Ref** | `Units`; clearance and watermark gap use `k` |
| `layer2.py:593-617` `place_band` as fraction of H | OK (fraction) | per-orientation value from the profile |
| `layer2.py:119,155,162,185,211,269,503,630,732` plain `W,H` reads | OK | none |
| `gates/props_gates.py:11,86` F04 `SAFE_X*W/1080, SAFE_TOP*H/1920` | **Ref** | `Units.safe()` |
| `draft.py:42-47` `OVERLAY_SLOTS` (4 slots at 1080x1920) | **Port** | table keyed by orientation (default in `units.py` or the profile `[draft.slots]`); `draft.py:384-387` `_slot` uses `Units`. |
| `draft.py:294` prompt "vertical documentary short", `draft.py:318-320`, `ai/tasks.py:46` "1080x1920 vertical documentary edit" | **Port** (text) | template on `{orientation}`, `{W}x{H}`; `min_seg_s/max_seg_s` come from the profile |
| `hyperframes.py:80-81` `CARD_TOP`, `TITLE_TOP` | **Port** (fractions of H) | profile values per orientation (`[theme.cards]`) |
| `hyperframes.py:512-575` scene dict: `width`, `height`, theme | **Twin source** | add `scene["k"]`, `scene["ref"]`, `scene["safe"]` (from `Units`) so JS never recomputes `W/1080` |
| `hyperframes.py:552` `camera3d.perspective_px(S.height)` | OK (fraction of H) | check tilt look in landscape (uncertain) |
| `hyperframes.py:519` `cover = max(W/iw, H/ih)` | OK |  |
| `hyperframes.py:822` template `__W__`, `__H__` | see 5.3 | |
| `camera3d.py:19-27` | OK | |
| `globe.py` (`TEX_W = 8192`, `PATCH_MAX = 4096`, `globe.py:29-30`) | OK for width of the texture; frame-size dependent `patch_plan(... S.width, S.height ...)` `:355` | uncertain at 3840x2160 (detail patch sizing) |
| `fx.py:61,89` `min(w,h)/1080.0` | OK (= `k`) | none; switch to `units.k` for readability |
| `fx.py:183,192,227` `min(W/1080.0, H/1920.0)` | **Ref**, wrong in landscape (gives 0.5625 at 1080p) | `units.k` |
| `fx.py:160,239-240` `height_pct/100*H`, `h > 0.5*H` | fractions of H | OK, but cutaway height is a fraction of the short side in portrait terms; check visually in phase 3 |
| `fx.py:359,366` particles `H * (...)` | fractions of H | OK for look; speeds in `H` units are fine |
| `transitions.py:37,201` `PAGE_SHADE_PX = 48`, `* W/1080` | **Ref** | `* k` |
| `transitions.py:264,272` size passes | OK | |
| `native.py:192-193,248` `scale=W:H:force_original_aspect_ratio=increase, crop=W:H` | OK (and better for 16:9 stock footage) | none |
| `native.py:158-170,255-302` work-scale and RGB24 buffers `ws*hs*3` | OK; memory x4 at 4K, see 5.4 | |
| `blocks.py:54,89,131` `width=1080, height=720` default block sizes (chart, timeline, infographic) | independent images | OK; blocks placed into overlays by size; check in landscape |
| `qa.py:62`, `gates/layer3.py:16,38,58` `SNAP_DIV = 4` | OK; R38 thirds: Rust picks top/mid/bottom for `h >= w`, left/centre/right otherwise (`pixel.rs:33,67-74`) | confirm the owner wants left/centre/right "empty thirds" for landscape (Q8); otherwise add a mode argument |
| `sheet.py:12,30,56` `TILE_DIV = 5` | OK (4K tile 768x432) | |
| `deliver.py:10-18,143-168,176-183` Shorts metadata (`#Shorts`, `"shorts": True`, platforms `youtube-shorts`, `instagram-reels`), thumbnail = middle frame at full size | **Port** | landscape: platforms `youtube`, no `#Shorts`, `shorts: false`, thumbnail 1280x720 JPEG under 2 MB (YouTube limit, from memory: verify) |
| `gates/style.py:289-291` D01 duration min/max | profile | landscape profile values (owner) |
| `gates/style.py:145-166` camera speed uses `W,H` | OK | |
| `importer.py:176` new project writes `1080x1920`; `cli.py:674` `--width default=1080` | **Ref** | `--format {short,landscape-1080p,landscape-1440p,landscape-4k}` sets width, height and profile hint |
| `profiles.py:303,587,617,637,774,810,818,831` doc comments "at 1080 / at 1920" | docs | reword to "reference canvas" |
| `profiles.py:657-703` print ranges (`title_px` 84..96, `year_cx` 0..1080, `year_cy` 520..600, `heading_baseline_y` 360..400, `label_px` 52..56) | **Port** (validators) | move the approved ranges into the profile or define them relative to the orientation default; landscape needs wider ranges |
| `profiles.py:1016-1024` `fixed_y`, `max_bottom_y` must be in `0..1920` | **Ref** | `0..ref_h` of the profile's orientation |
| `profiles.py:1018-1021` `place_band` 0<lo<=hi<1 | OK | |
| `profiles.py:457-486` `RenderProfile` x264 fields | see 5.1 | add `final_level` (optional) |
| `profiles.py` `_check_python_keys`, `_check_safety` | check that landscape overlay values pass the "safety" comparison against the base | uncertain; read before phase 2 |
| `gate_registry.py:69,99` text of O01 and R56 ("26 px at 1920 height", "100 px of camera motion at 1080x1920") | docs strings generated into GATES.md | reword |
| `resources.py:32` `RAM_PER_WORKER_GB = 1.8` | **Ref** (memory per worker at 1080x1920) | scale by pixel count ratio, see 5.4 |

### 3.3 Rust (`rust/hypoverum_core_rs/src`)

| File:line | Assumption | Proposed change |
|---|---|---|
| `ass.rs:20,51,66` `Style::default()` `size 84`, `margin_x 90` (and outline 5, shadow defaults) | **Ref**: defaults are 1080x1920 px | no change in Rust (changing defaults would alter the portrait `.ass`); Python scales explicit values (see `rust_style`). Alternative if the owner prefers Rust ownership: add `scale: f64` to `compile_ass` and multiply inside; changes the PyO3 signature and `ALGO_VERSION`, so cache keys would change. Not recommended. |
| `ass.rs:177` `SLOTS = lower 0.70, mid 0.50, upper 0.24` | **Port** | leave; the landscape profile sets `fixed_y` (or a new `slot_y` fraction handled in Python), so Rust is untouched |
| `ass.rs:121-125` `margin_x < width/2`, `:356-360` `PlayResX/Y = w,h` | OK | |
| `ass.rs:529-812` tests use `1080, 1920` | keep; add 2 landscape tests (1920x1080 and 3840x2160 compile without error and `est_width <= w - 2*margin_x`) | |
| `camera.rs:73,198-204` `endpoints`, `motion_px` use `ow.hypot(oh)` | OK (diagonal) | none; tests at :285-370 keep portrait numbers, add a 16:9 case with the same diagonal |
| `camera.rs` cover-crop of a 16:9 source into a 16:9 frame leaves no pan slack | uncertain (presets `pan_*` need zoom to move) | verify in phase 2; R56 auto-raises intensity today (`layer2.py:211`) |
| `pixel.rs:33,67-74` thirds by orientation | OK (already landscape-aware) | see Q8 |
| `proxy.rs:10,34,230` `HEIGHT_RANGE 64..=1080` | OK for an analysis proxy of any footage orientation | none |
| `frames.rs`, `silence.rs`, `num.rs`, `lib.rs` | OK | |
| `docs/RUST.md:28,78` | mentions proxy range | unchanged |

### 3.4 JavaScript (`video_engine/hf/*`, `video_engine/template/index.html`)

JS reads `window.SCENE` (`hyperframes.py:831`) and has globals `W`, `H`, `FPS`.

| File:line | Assumption | Proposed change |
|---|---|---|
| `hf/fx_bubbles.js:8` `sx=60*W/1080, st=100*H/1920, sb=H-100*H/1920` | **Ref**, **Twin of** `layer2.py:24-26`, `watermark.py:14`, `overlays.py:27` | `const {x:sx, top:st, bottom:sb} = S.safe` (Python fills it from `units.safe_area`) |
| `hf/fx_bubbles.js:13,50` `font_px*H/1920`, `gap_px*H/1920` | **Ref**, twin of `fx.py` bubbles | `* S.k` |
| `hf/fx_timezones.js:7` same safe-area line | **Ref**, twin | same |
| `hf/fx2.js:43` `placeInset`, `:208-227` particles `H*(...)` | fractions of H | OK; inset margin/gap come from Python (`fx.py`) already scaled |
| `hf/fx_backdrop.js:19,28` grid cell `G.cell_px` from `fx.py:57` (`cell_pct/100*h`) | cell = % of H; in landscape a cell is 1.78x narrower relative to width | define cell as % of the short side for both orientations (portrait: W, so `cell_pct` semantic changes in portrait; instead keep `H` for portrait and add `cell_basis`): decision Q9 |
| `hf/globe.js:17-18` `setSize(W,H)`, `PerspectiveCamera(G.fov, W/H)` | aspect aware; vertical fov fixed so a landscape frame shows a wider slice | globe radius relative to frame is set by `G.fov` and `globe.key_motion`; check visually, scale `fov` per orientation in the profile (uncertain) |
| `hf/parallax.js`, `hf/props.js` | `W/2`, `H/2` only | OK |
| `hf/dom_probe.mjs:33`, `props_probe.mjs:16` `setViewport({width: W, height: H, deviceScaleFactor: 1})` | probe at full output size | at 4K keep viewport = output size and DSF 1 unless option B of 5.3 is chosen |
| `hf/model_render.mjs:18` sprite viewport `size*ss` | OK | |
| `template/index.html:5,12,13` `__W__/__H__` viewport and root size | OK | see 5.3 |
| `template/index.html:195` `BOXR = label_box_radius_px * S.width/1080` | **Ref** | `* S.k` |
| `template/index.html:329,331,332` `glow_px*S.width/1080`, `edge_px*S.width/1080` | **Ref** | `* S.k` |
| `template/index.html:168,531` `S.width`, `S.height * pin_height_pct/100` | OK / fraction | OK |
| `template/index.html` 31 uses of `T.*_px` (for example `T.route_width_px` :365-391, `T.region_edge_px` :300-302) and the CSS in the first 60 lines (`.pin .dot width:26px` :18-24) | **Ref, unscaled**: these are absolute px at whatever output size. At 2160x3840 they would be half the visual size of 1080x1920 (uncertain whether `hyperframes.py` pre-scales the theme: no scaling was found in `hyperframes.py:512-575`). | Decision in 5.3: either scale in `hyperframes.py` when building `scene["theme"]` (multiply every `*_px` by `k`, a whitelist table in `units.py`), or render the page at the reference viewport with `deviceScaleFactor = k` and leave all CSS untouched (preferred if the HF CLI allows it). |

### 3.5 Profiles (`profiles/*.toml`, `profiles/geoverum/*.toml`, `profiles/taste/*`)

| File:line | Assumption | Proposed change |
|---|---|---|
| `default.toml:18` `clearance = 160` "at 1920 height" | **Ref** | stays, documented as reference canvas; landscape 90 |
| `default.toml:108` `label_box_radius_px = 3` "at 1080 px frame width" | **Ref** | reword |
| `default.toml` `[fx.*]` (lines 145-220) and `[theme]` px fields | **Ref**, orientation-neutral lengths | none |
| `geoverum.toml:30,31,34` `fixed_y 1382`, `max_bottom_y 1480`, `place_band [0.60,0.76]` | **Port** (Shorts UI) | landscape file/overlay (2.3) |
| `geoverum.toml:15-36` `ab_sizes=[84]`, `outline=0`, `shadow_*`, `max_words=2` | size **Port**; `max_words=2` is a Shorts rhythm | landscape: size ~58, `max_words` 3..4 (owner) |
| `geoverum.toml:44-56` `[draft]` rules (e.g. "Open on a moving map", "Something new on screen every 1-3 s", last 5 s keyword) | **Port** (Shorts pacing, text) | landscape draft rules from the owner |
| `geoverum.toml:119-130` `[render]` `final_maxrate_kbps = 25000`, `hf_crf 8`, `segment_crf 8` | sized for 1080x1920, ~300 MB per 90 s | per-resolution caps, see 5.1 |
| `geoverum.toml:131-170` `[gates]` hook/camera/silence/cta | rhythm gates for Shorts | landscape thresholds from the owner (hook window, camera hold times) |
| `geoverum.toml:176-178` `[gates.duration] 45..90` | Shorts | long-form range |
| `geoverum.toml:180-` `[watermark]` `px 40`, top right | **Ref/Port** | landscape corner/px |
| `geoverum/layout.toml:4,12-17,5-10,19-27` zones, grid, sizes (`title 112`, `year 128`, `pin_label 52`) | **Port** | new `geoverum/landscape/layout.toml` (Step A) or `[landscape.layout]` (Step B) |
| `geoverum/layout.toml:44-54` labels `offset 16`, `edge_clear 24`, ... | orientation-neutral lengths | reuse |
| `geoverum/print.toml:6,14-30` `watermark_clear_px = 32`, `title_px = 88`, `year_*`, `heading_*` | `title_px` etc. **Ref**; `year_cx/cy`, `heading_baseline_y` **Port** | landscape print file (2.3) |
| `geoverum/sound.toml`, `entities.toml`, `legibility.toml` | OK | reuse by `include` |
| `profiles/taste/geoverum.json` | prose rules ("2D side-view cross-section") | review for portrait-only wording (uncertain; not read in full) |

### 3.6 Documentation

| File:line | Change |
|---|---|
| `README.md:4` "H.264 1080x1920" | "1080x1920 (Shorts) or 1920x1080 / 2560x1440 / 3840x2160" |
| `docs/TIMELINE.md:114,136,140` (overlay font "26 px at 1920 height", caption "84 at 1920 px height", `clearance`, `fixed_y`, `max_bottom_y`) | "reference canvas" wording, link here |
| `docs/SPEC.md:31` "finished vertical video"; `:150` `max_bottom_y`; `:464` "Units: output px at 1080x1920 (scaled for other sizes)"; `:510` example settings | units section rewritten around `Units` |
| `docs/PROFILES.md:35,109` watermark, key ranges | new `[format]`, `[landscape]` / landscape profile, ranges |
| `docs/GATES.md:83,97,199,210` (generated from `gate_registry.py`; also `scripts/check_docs.py`) | regenerate after rewording registry text |
| `docs/GLOSSARY.md:36` "Safe area: 60 px / 100 px (1080x1920)" | per orientation |
| `docs/RATIONALE.md:154` Geoverum caption limits and Shorts safe zones | add the landscape rationale |
| `docs/RUST.md:28,78`, `docs/CLI.md` (`--width/--height`, new `--format`), `docs/DELIVERY.md` (Shorts metadata), `docs/AI.md` | as needed |
| `docs/proposals/formats-and-fps.md` | remove option 2 once built; link to this file |
| `docs/README.md` index | add LANDSCAPE.md row (`scripts/check_docs.py` may require it; uncertain) |
| `AGENTS.md`, `CHANGELOG.md` | after owner approval of the work |

---

## 4. Gates

Style gates and technical gates that carry reference-px thresholds or portrait assumptions (IDs from `gate_registry.py`, `docs/GATES.md`):

| Gate | Threshold / assumption | Landscape change |
|---|---|---|
| O01 drawn overlays | text >= 26 px at the reference height; safe area 60/100; no overlap | `MIN_FONT_PX` x `k`; safe from `Units` |
| R56 camera motion | 100 px at the 1080p-class diagonal | unchanged (diagonal is equal). Auto intensity raise stays. 16:9 sources into 16:9 frames may need more intensity (uncertain) |
| R62 caption margins | est width <= W - 2*margin_x; `size` default 84, `margin_x` default 90 | scaled effective style; the landscape profile supplies size/margin |
| R63 element margins and collisions | safe area, `clearance` px, `place_band` | `Units`; `place_band` per orientation; `clearance` scaled by `k` |
| Z01 caption bottom limit | `max_bottom_y` px at 1920 (Shorts UI) | absent in landscape; wording "Shorts UI" only in the portrait profile |
| B01 watermark clearance | `watermark_clear_px` 32 px at 1920 height; watermark top-right | `k`; watermark corner configurable; rect from `watermark.placement` stays the single source |
| F04 prop safe area | `SAFE_X*W/1080`, ... | `Units.safe()` |
| L01..L10 layout | everything in `[layout]` (zones, grid, sizes, labels, timing) | per-orientation layout profile; code paths use `kx/ky` which equal `k` at 16:9. L06 zones and L09 caption ground need the landscape planner spike (phase 4). A beats scene under a profile without `[layout]` fails L06 (`docs/SPEC.md`) so a landscape profile with no layout simply cannot use beats scenes |
| R38 empty thirds / black frame | Rust thirds by orientation | already landscape-aware; confirm intent (Q8) |
| M02 map detail | map px per on-screen px at deepest zoom (>=1.2 warn, <0.85 fail, `layer2.py:30-31`) | OK, but 4K output needs map sources about 4x the pixels for the same zoom: M02 will fail on 1080p-class maps. This is correct behaviour and a cost (5.5) |
| M04 globe resolution (`layer2.py:119`) | `TEX_W 8192` | at 4K the globe needs the detail patch more often; check |
| D01 duration | profile `min_s/max_s` | profile |
| P01 camera hold/moving, hook gates, silence, CTA, keyword return | seconds based | portrait-agnostic mechanically; thresholds are brand rhythm, owner decides for long-form |
| E01, T01 (max typefaces), S01, A02 etc. | no geometry | none |
| new U01 | frame aspect differs from the reference by > 1 % | advisory finding (2.1) |
| new profile/format check | profile orientation vs settings orientation | `EngineError` at timeline load |

---

## 5. Encoding and performance at 4K

### 5.1 x264 level, bitrate, preset

Current: `config.x264()` (`config.py:58-63`) sets no `-level` (x264 signals the lowest level that fits the stream), `-pix_fmt yuv420p`, BT.709 tags, optional `-maxrate` with `-bufsize 2*maxrate`. Geoverum final: CRF 12, `slower`, cap 25 Mb/s (`geoverum.toml:126-129`).

H.264 level arithmetic (macroblocks of 16x16; from the H.264 level table, memory, verify against ffprobe):

| Output | MBs/frame | MB/s at 30 fps | MB/s at 60 fps | Level 30 fps | Level 60 fps |
|---|---|---|---|---|---|
| 1920x1080 | 8160 | 244 800 | 489 600 | 4.1 | 4.2 |
| 2560x1440 | 14 400 | 432 000 | 864 000 | 5.0 | 5.1 |
| 3840x2160 | 32 400 | 972 000 | 1 944 000 | 5.1 (max 983 040) | 5.2 (max 2 073 600) |
| 1080x1920 (now) | 8160 | 244 800 | 489 600 | 4.1 | 4.2 |

Recommendation: leave the level automatic (no `-level` flag) so x264 picks 5.1/5.2, but add an assertion in `streams.verify` that the encoded `level` is <= 5.2 and a profile field `final_level` (optional) for devices that need a fixed value. 3840x2160 at 30 fps is within level 5.1 only with fewer than about 4 reference frames; x264 handles this itself.

Bitrate: YouTube's recommended SDR upload bitrates (from memory, to be re-checked on the YouTube help page): 1080p about 8 Mb/s, 1440p about 16 Mb/s, 2160p 35..45 Mb/s at 30 fps. The engine's file is an upload master that YouTube re-encodes, so the cap can be several times higher. Proposed `final_maxrate_kbps` in the landscape profile, scaled by pixel count from the portrait 25 Mb/s: 1080p 25 000, 1440p 45 000, 2160p 100 000; `bufsize` stays `2 x maxrate`. Expected size (cap, upper bound): 25 Mb/s x 600 s = 1.9 GB per 10 min at 1080p; 100 Mb/s x 600 s = 7.5 GB at 4K. CRF 12 `slower` is typically below the cap for map content (uncertain). The field `final_maxrate_kbps` allows up to 200 000 (`profiles.py:475`), enough.

Preset: 4K `slower` is roughly 3 to 5 times the time of 1080p `slower` per frame (pixel count 4x plus more motion search; estimate). The final mux "runs alone" with up to 6 threads (`resources.py:10`). Allow `final_preset = "slow"` for 4K drafts of the landscape profile; owner decides final quality (Q4).

Intermediates: `segment_crf = 8`, `medium` in Geoverum is near lossless. At 4K a 90 s portrait segment set that is a few hundred MB becomes several GB. Proposal: for resolutions above 1440p use `segment_crf = 14, segment_preset = veryfast` (the engine default is 16 veryfast, `config.py:34`), which the config comment says matches CRF 18 medium after the final pass.

### 5.2 Render at 1080p and upscale vs native 4K

| Option | Cost | Quality | Risk |
|---|---|---|---|
| **A. Native 4K everything** | 4x pixels in every stage: HF capture, Rust warp, overlays (Pillow), RGB24 buffers (24.9 MB per frame vs 6.2 MB), grade, x264 | best for text, vector lines (routes, region edges), captions; source maps and stock must have enough pixels (M02) | disk, time, memory (5.4) |
| **B. Render at 1920x1080, upscale in the final mux (`scale=3840:2160:flags=lanczos`)** | about 1x of a 1080p build, plus one 4K encode | text and vector detail only 1080p quality; YouTube gives 1080p uploads the lower-quality VP9/AV1 4K ladder only if the upload itself is 4K, so an upscaled 4K master mainly buys YouTube's better 2160p encoding tier, not detail | cheap; honest about being upscaled |
| **C. Hybrid: native 4K for text/overlay/caption layers, 1080p for the heavy video layer** | between | captions and overlays are sharp; the map video is soft | the engine renders text on top of video inside the HyperFrames page for map scenes (`hf` path) so hybrid is only natural for native segments + `.ass` captions; large change, not recommended now |

Recommendation: ship 1080p and 1440p native first (phases 1 to 3), implement option B as a delivery option `--upscale 2160` (a final-mux scale, small change in the composite step; cache keys for segments unchanged), and treat native 4K as phase 5 after measuring a real 4K build of `examples/minimal` and one map scene. Captions as `.ass` burn at the final output size in the composite pass, so in option B they must be rendered at 4K PlayRes (compile the `.ass` for the output size, not the segment size) to avoid soft text: the composite pass would take the 4K size for `.ass` while segments stay 1080p. This is a detail to verify in `build.py` (uncertain; not read).

### 5.3 HyperFrames and Chrome at 4K

Facts (confirmed): the page is sized by `__W__` x `__H__` (`template/index.html:5,12,13`, `hyperframes.py:822`); the DOM probe uses `setViewport({width: W, height: H, deviceScaleFactor: 1})` (`dom_probe.mjs:33`); the HF CLI is called as `hyperframes render <dir> --fps --sdr -q <quality> --crf <n> --workers <n> -o <file>` (`hyperframes.py:905`) with no size flag, so the size comes from the composition (uncertain how the CLI derives it: not inspected, vendor missing).

Two ways to run 4K:

- **Option 1, native viewport 3840x2160, DSF 1.** Simple, what the code does today for any size. All CSS lengths must be scaled by `k` (3.4 and 5.3 lists): about 31 `T.*_px` uses, CSS rules in the template head, plus the JS twins. Chrome screenshots of 3840x2160 JPEG q100 cost roughly 4x memory and time per frame (estimate). SwiftShader (software WebGL, `model_render.mjs:11`, globe scenes) at 4K canvas is slow (uncertain).
- **Option 2, reference viewport + `deviceScaleFactor = k`.** The page is authored at 1920x1080 CSS px with DSF 2 (4K) or 1.333 (1440p). CSS lengths, theme px and JS math stay in reference px; Chrome rasterises at the output size. All `T.*_px` and `W/1080` sites stop mattering. The DOM probe must then convert CSS-px rects to output px (multiply by `k`) before the layout gates compare against plans in output px. Needs the HF CLI to expose a scale or `deviceScaleFactor` (uncertain; if it does not, a patched wrapper around puppeteer-core would be needed, which conflicts with the pinned HF toolchain).

Recommendation: implement Option 1 (known to work with the current CLI, and it also fixes the unscaled-theme issue at every size) with the whitelist-scaling of theme px in Python; evaluate Option 2 in the phase 0 spike and adopt only if the CLI supports it.

Chrome flags and limits: GPU is disabled (`--disable-gpu`, `dom_probe.mjs:25`), so 4K canvas ops run on the CPU. Each HF worker holds a 4K page: budget more RAM per worker (5.4). Texture sizes: `TEX_W = 8192` is SwiftShader's limit (`globe.py:29`), unaffected by output size.

### 5.4 Memory, disk, time

- RGB24 frame buffers: 1080x1920 = 6.2 MB; 1920x1080 = 6.2 MB; 2560x1440 = 11.1 MB; 3840x2160 = 24.9 MB (`native.py:170,255-302`, `transitions.py:230-244` raw pipes). Per-segment `n_frames x size` buffers are not held (frames stream through pipes; uncertain for transitions).
- Workers: `resources.py:32` assumes 1.8 GB per worker at 1080x1920. Proposal: `ram_per_worker = 1.8 * (W*H) / (1080*1920)` capped, so 4K gets about 7 GB per worker; on the 4-CPU, 15 GB audit host that means 2 workers at 4K (`clamp(min(cpu, RAM // per_worker), 2, 64)`). The "x264 thread counts are part of the encoded bytes" rule (`resources.py:13-14`) must not be disturbed: only the worker count changes, not `X264_THREADS`, so portrait stays identical.
- Disk: AGENTS.md says the disk is about 80 % full and the wrapper refuses to start below 8 GB free. Order-of-magnitude (estimates, not measured): a 10 min 4K build with near-lossless intermediates can write 10..20 GB of segments plus the HF JPEG frames in `/dev/shm` (RAM!) per active segment. `/dev/shm` is `VIDEO_ENGINE_SHM`; 4K HF frames at q100 are about 1.5..3 MB each, so a 60 s HF segment at 30 fps is 2.7..5.4 GB of tmpfs (uncertain; HF may stream). Mitigation: a preflight "estimated disk" check in `engine-build` (`pixels x fps x seconds x factor`), and `segment_crf 14` above 1440p (5.1).
- Time: build time scales roughly with pixel count, so 4K is about 4x a 1080p build for the pixel-bound stages (HF capture, x264) and about 1x for the audio, STT and gate math. A 90 s Geoverum Short is a few minutes of render (not measured here); a 10 min video at 1080p is on the order of 7x that, and 4K another 4x. Measure in phase 0 with `examples/minimal` and one HF map segment before promising numbers.
- Caches: segment keys include W, H and the profile hash, so landscape and portrait never collide. Disk cost per cached segment grows 4x at 4K; `/workspace/cache/engine` is large already; do not prune without the owner (AGENTS.md).
- Gates: SNAP_DIV 4 and `SAMPLE_STEP` 5 keep gate cost lower than render cost (4K snapshot 960x540).
- Sources: M02 (map detail) and still-image cover scaling need 4K-worthy sources. Epidemic/stock footage at 4K may be absent; a 1080p source into a 4K frame is a 2x upscale (`native.py:192`, flags=bicubic); decide quality policy (Q10).

---

## 6. Migration and test plan

### 6.1 No-change guarantees for portrait

1. `Units` reproduces the legacy expressions exactly (`W / 1080`, `H / 1920`); tests `tests/test_units.py` assert float equality on a grid of sizes (1080x1920, 720x1280, 540x960, 2160x3840) against inline legacy lambdas.
2. Profile hash: assert `profiles.load("geoverum").blake3` (or the hash used in `manifest.py` / `build_manifest.json`) is unchanged before and after the change, and that adding `[format]` or `[landscape]` to a profile does not alter the portrait dump. Pattern: `tests/test_taste_lock.py`, `tests/test_segment_key.py`.
3. Segment cache keys for a fixed timeline at 1080x1920 unchanged: `tests/test_segment_key.py`, `tests/test_native_camera_cache.py`, `tests/test_manifest_build.py`.
4. `examples/minimal` build at 1080x1920: compare `final.mp4` stream hash (video and audio `-f md5`) and `build_report.json` (minus timestamps/build_id) against a recorded baseline taken before phase 1, on the same host and thread budget.
5. Rust: `cargo test --release --no-default-features` unchanged (49 tests); only additive tests.
6. The existing tests that must stay green and unmodified (other than new parametrisations): `test_layout.py`, `test_regions.py`, `test_routes.py`, `test_pins.py`, `test_gates_layer2.py`, `test_gates_layer3_qa.py`, `test_style_gates.py`, `test_fx_*.py` (grid, stars, timezones, geoid, hatch, cutaway, flag characters), `test_props.py`, `test_blocks_labels.py`, `test_camera3d.py`, `test_max_zoom.py`, `test_texel_density.py`, `test_legibility_lines.py`, `test_rust_core.py`, `test_ai_providers.py`, `test_taste_lock.py`, `test_check_docs.py` (the 106 test files run under `pytest -q -n 4`; per AGENTS.md avoid the full suite until the end of a phase).

### 6.2 New tests

- `tests/test_units.py`: orientation, `k`, `safe()` per orientation, `U01` threshold, 9:16 and 16:9 uniformity at 1080p / 1440p / 4K.
- Golden tests per gate at landscape sizes (GOOD/BAD per gate, as `docs/TESTING.md` requires): O01, R62, R63, Z01 (absent limit), B01 (corner), F04, L01..L10 on a landscape beats plan, M02 at 4K.
- `tests/test_captions_landscape.py`: `rust_style` scaling at k = 1, 1.333, 2 (also 2160x3840 portrait if the existing gap is confirmed); R62 and Z01 agree with the `.ass` events.
- JS twins: a headless check (the DOM probe tests already need Chrome and skip otherwise) that `fx_bubbles` and `fx_timezones` safe rectangles equal `units.safe_area` at 1920x1080 and 3840x2160.
- Profile tests: `geoverum_landscape` loads, resolves, is rejected on a portrait timeline and vice versa; validator ranges.
- Example project `examples/landscape/` (offline, like `examples/minimal`): 1920x1080, 3 segments (a native still with camera move, a HyperFrames card/title scene, a map scene once phase 4 is done), pinned captions, `prepare.sh` generating assets. A `--size` option in `prepare.sh` writes `settings` for 1440p and 4K runs. The README of the example lists the expected gate output.
- Visual review: `engine sheet` and `engine preview P --seg N --time S` at 1080p; side-by-side contact sheet of portrait vs landscape for the owner.

### 6.3 Phased rollout and effort

Estimates are engineer-days of focused work by someone who knows the codebase; the HyperFrames and layout phases carry the most uncertainty.

| Phase | Content | Exit criterion | Effort |
|---|---|---|---|
| 0 Spike | install toolchain on a scratch host; render `examples/minimal` at 1920x1080, 2560x1440, 3840x2160 and 2160x3840 with the current code; record the failures, the caption-scaling question, HF CLI size/scale options, level (ffprobe), time, RAM, disk | written baseline numbers and a list of real gaps | 1..2 |
| 1 Units refactor (portrait-neutral) | `units.py`; replace the literal sites in 3.2 and the four safe-area copies; byte-identical baseline; `U01` | all section 6.1 checks green | 3..4 |
| 2 Landscape technical path | `[format]`, profile validation, `geoverum_landscape` (step A) with captions, safe area, watermark corner, overlay slots, `rust_style` scaling; native-only landscape project passes gates at 1080p/1440p | `examples/landscape` (native segments) builds clean | 4..5 |
| 3 HyperFrames and JS | `scene.k/ref/safe`, template and theme scaling, JS twins, `CARD_TOP/TITLE_TOP`, fx scale; DOM probe at landscape | HF title/card/fx scenes at 1080p and 1440p; probe equals plan | 3..4 |
| 4 Layout planner for landscape | landscape zones/grid in data; adapt planner where it assumes a vertical stack (L06, L09, title placement); Geoverum map scene in landscape | L01..L10 pass on a landscape map scene | 4..8 (uncertain; could be larger if the planner needs new geometry) |
| 5 4K | resource scaling, disk preflight, maxrate/segment CRF per resolution, `--upscale 2160`, native 4K measurements, optional DSF option | one 2-minute 4K build, measured time/disk; documented | 3..4 |
| 6 Delivery, draft, docs | `deliver.py` metadata and thumbnail, `draft.py` slots and prompts, CLI `--format`, docs in 3.6, `check_docs.py`, CHANGELOG | docs build, delivery tree for landscape | 2..3 |
| 7 (optional) Step B | `[landscape]` overlay in one profile | duplicate profile files retired | 2..3 |

Total about 20..30 days; phases 0..3 (usable landscape for non-map content) about 11..15.

Per AGENTS.md: no code, commit, merge or push without the owner's explicit permission; each phase is a separate change set for the owner to approve; build tests go to `/workspace/_scratch/`, builds use `engine-build`, and the disk is kept under watch.

---

## 7. Open questions for the owner

**Decided by the owner (2026-11-02):**

- Q1: every size (1080p, 1440p, 4K) is rendered natively; no upscale path. 60 fps is required (x264 level 5.2 at 4K60).
- Q2: a landscape version of Geoverum, usable for a new channel as well (so landscape values live in their own
  profile that other channels can include).
- Q4: no file-size limit; quality targets decide the bitrate caps.

Still open: Q3 and Q5 to Q14 below.

1. **Formats in scope.** 1080p, 1440p and 4K all native, or 1080p native and 4K as an upscale delivery option first (5.2)? Is 60 fps required (level 5.2, doubling cost)?
2. **Which channel and look.** A landscape Geoverum (map-led, same brand) or a new channel? Are the landscape values in 2.3 acceptable as a starting point (safe area 96/54, caption 56..60, watermark bottom right)?
3. **Square and 4:5.** Should 1080x1080 or 1080x1350 be supported as a format, or rejected with an error instead of the advisory `U01`?
4. **Quality targets and size.** Final CRF, preset and bitrate caps for 1440p and 4K (5.1)? Is a 7 GB file per 10 min acceptable for the Drive hand-off (the Projects Manager bot handles upload, `docs/LIMITS.md`)?
5. **Grid.** Landscape Geoverum grid: 12 columns centred, or another count? How should the title and data card sit in landscape (top band, or a left column) given the vertical Z1/Z2 stack assumption?
6. **O01 minimum overlay font.** Keep 26 px at the reference canvas (so 26 px at 1080 height in landscape, much smaller relative to the frame than 26 px of 1920 in portrait) or define the minimum per orientation?
7. **Caption scaling.** Is it intended that caption `size`, `margin_x`, `outline` and `shadow` are not scaled for non-1080 sizes today (the docs say "84 at 1920 px height")? If it is a defect, fixing it changes the look of 2160x3840 Shorts (cache keys for that size would change); portrait 1080x1920 is unaffected.
8. **R38 thirds.** The Rust stats use left/centre/right thirds for landscape. Is "no empty third" the right check for landscape content?
9. **Grid backdrop cell.** `fx.grid.cell_pct` is a percentage of H today. For landscape should it be a percentage of the short side (portrait unchanged by using W... which would change portrait) or keep H in both (cells much smaller relative to the width)? Needs a design decision.
10. **Source quality policy at 4K.** Fail (M02) or warn when sources are below 4K-class resolution; is upscaled 1080p stock acceptable in a 4K master?
11. **Profile mechanism.** Step A (separate `geoverum_landscape.toml`) now and Step B later, or go straight to the `[landscape]` overlay?
12. **Delivery.** Landscape metadata template (title, description, tags, no `#Shorts`), thumbnail rule (middle frame is rarely a good YouTube thumbnail), chapters/SRT expectations for long videos?
13. **Duration and rhythm gates.** Hook, camera-hold, silence and CTA thresholds for long-form; Geoverum's values (hook 0.2..3 s, hold 1.7..3 s, duration 45..90 s) are Shorts rhythm.
14. **Worktree and disk.** The spike needs a scratch render host or `/workspace/_scratch/` space for several GB; confirm where it may run (AGENTS.md: render tests only in `/workspace/_scratch/`).

---

## 8. Risks

- **Unscaled theme px in the HyperFrames template** (31 `T.*_px` uses, template CSS) may mean 4K portrait is already visually wrong; phase 0 settles it. Fixing it for portrait at non-1080 sizes changes those renders (not 1080x1920).
- **Layout planner** was written for a vertical stack; landscape map scenes may need code, not just data (phase 4).
- **HF CLI** size/scale handling is unverified (vendor not installed in the audit environment).
- **Memory and disk** at 4K on the current host (4 CPUs, 15 GB RAM, disk about 80 % full per AGENTS.md; `df` in the audit sandbox showed a different disk, so the real host is unmeasured).
- **Cache and hash drift**: any change to a value that enters the profile dump or a cache key for portrait must be caught by the section 6.1 tests before merge.
