# Geoverum camera: what two reference Shorts do

Status: analysis and proposal, nothing built. Measured 2026-11-02 for Arundaya, who named these two Shorts as the
camera motion wanted for Geoverum Shorts.

| Ref | Video | Format |
|---|---|---|
| A | "Who Owns Antarctica? Nobody's Land" (KXvobbCxeRQ) | 60.0 s, 608x1080, 30 fps, AV1 |
| B | "The Longest Road in the World: Pan-American Highway" (r4BuA9MNq3E) | 60.0 s, 608x1080, 30 fps, AV1 |

## Method

`scripts/reference_camera/` (needs `opencv-python-headless`, `scipy`, `matplotlib`; not engine dependencies):

1. `motion.py`: every frame pair at 304x540: Shi-Tomasi corners, pyramidal Lucas-Kanade flow, RANSAC similarity
   (zoom, rotation, frame-centre pan) and homography (perspective), mean frame difference and histogram distance for
   cuts. The burned-in caption band (66-76 % of the height) is masked, because static text pulls the estimate to zero.
2. `summarize.py`: cuts, shots, moving share with the P01 thresholds, holds, speed plots.
3. `moves.py`: splits the screen speed (pan + |zoom| x half the frame diagonal, frame widths per second, the P03
   measure) into moves at prominent peaks and fits each move's progress curve to standard eases.

Limits: the estimate follows the dominant layer, so animated overlays (flags filling, pie sectors growing) and the
rotating 3D globe in A (2-10 s) add noise; those spans were checked by eye on frame strips instead. Every claim
below was checked against frame strips.

## Findings

### Overall

| Measure | A (Antarctica) | B (Pan-American) | Geoverum today |
|---|---|---|---|
| Cuts in 60 s | 14 (most inside zoom-blur transitions) | **0**: one continuous camera | a hard cut per visual-type change |
| Longest shot | 22 s (globe -> infographic) | 60 s | one segment per beat |
| Moving share (P01 thresholds) | 83 % | 87 % | gate: warn < 80 % |
| Longest hold | 1.57 s | 1.07 s | gate: warn > 1.7 s, fail > 3 s |
| Screen speed p50 / p90 (frame widths/s) | 0.15 / 1.36 | 0.25 / 0.85 | drift 0.06 ln/s ~ 0.06 fw/s |
| Moves (excluding transition spikes) | 17 | 18 (one every 3.3 s) | pulses every wait |
| Move length median (p25-p75) | 1.6 s (1.0-3.3) | 3.0 s (2.2-4.1) | pulse 0.8 s, whip 0.5 s |
| Peak speed median | 0.75 fw/s | 0.64 fw/s | pulse peak 1.06 ln/s |
| Largest zoom in one move | 12x in (after a transition) | 20x out (35-40 s) | max_zoom 16 |

### B: one continuous journey (the main reference)

The whole Short is one camera flying over one satellite map along the route; the story moves by moving the camera,
never by cutting.
Every move is one-directional: it reframes to the next place and stays there; there is no push-in-and-back.
The 18 moves fall into three kinds:

1. **Travel moves** (most): 2-4 s, pan + moderate zoom (0.6-2.6x), symmetric in-out ease (sine / power2 / power3
   in-out; the engine's `glide` fits about as well). Examples: 0-2.5 s, 4-6 s (Eurasia -> Alaska, 2x in),
   32-35 s (2.4x in).
2. **Reveals** with a long settle: fast start, then a long ease-out tail (power2.out / power3.out): rise 20-45 % of
   the move, fall 55-80 %. Examples: 8.9-12.8 s (rise 0.5 s, fall 3.4 s); 43.2-51.7 s, following Chile south while
   zooming out 7x (rise 2.2 s, fall 6.3 s); 54.4-59.9 s (final settle on the whole route).
3. **Big jumps**: 1.5-5 s, peak 2-3.5 fw/s with visible motion blur at the peak, no cut:
   - 2.5-4.0 s: whip pan Eurasia -> Americas, 1.4 frame widths in 1.5 s;
   - 26-29 s: zoom 5x into the Darien Gap (peak 3.3 fw/s at 27 s, then a 2 s ease-out);
   - 35-40 s: zoom out 20x from the Darien close-up to northern South America while panning south.

Moves are chained: 5 of 18 start before the previous one has stopped (speed at the hand-over 20-85 % of the peak),
so the camera rarely stands still; when it does (10.5-12.8 s, 56-60 s) a slow drift remains.
Graphics are timed to the camera: a country's flag fill and name appear while the camera decelerates onto it
(e.g. Colombia at 39-40 s, Darien labels at 28-29 s), never during the fast part.

### A: scenes joined by zoom-through transitions

A mixes scene types (3D globe, flat infographic on a blueprint grid, a tilted 3D paper map with a ship, a world
map, the globe again) and joins most of them with a **zoom-through**:
the outgoing shot accelerates into a zoom (about 4x in 0.3-0.5 s, power2.in), a radial zoom blur and sometimes a
white flash cover the cut, and the incoming shot lands from a large zoom with a strong ease-out
(29.8 s: 12x over 3.5 s, power4.out; 47.2 s: expo.out; 55.6 s: power4.out).
Inside a scene the camera re-frames on each narration beat: on the pie chart (33-46 s) one move every 3.3 s,
2.9-3.4 s long, alternating in and out (0.7x, 1.0x, 2.2x, 0.5x), power2/power3 in-out, near-zero speed between
moves for about 0.3 s. The 3D scenes add perspective tilt (homography term up to 0.4 on the pie, 3.4 in a transition).

## What this means for the engine

What already matches:

- P01 thresholds (holds, moving share) fit both references.
- Multi-key `camera_path` with `glide` legs keeps the speed continuous at inner keys (the slower leg's mean), which
  is B's chaining.
- The `settle` move and layout beats time entries to the camera's arrival, as B does with flags and labels.
- `zoom_whip` is a small zoom-through (scale 1.3, 0.1 s out, 0.4 s in, radial blur).

What differs:

| # | Reference | Geoverum today | Proposal |
|---|---|---|---|
| 1 | One map, one camera for the whole journey (B) | a hard cut at each visual-type change, `state` dissolve between map segments | Author a journey as one map segment whose `camera_path` has a key per narration beat (about every 3 s); keep cuts for real scene changes |
| 2 | One-directional reframes; no push-and-back | `[camera]` pulses (0.3 ln in and back in 0.8 s) in every wait | A/B the pulses off for journey segments (`pulse_zoom = 0`), keeping the drift |
| 3 | Reveals: fast start, long ease-out tail (rise 20-45 %) | `glide` (symmetric) and `whip` (fast in-out) | A new leg move, e.g. `reveal`: power3.out over 2-6 s, start speed taken from the previous leg |
| 4 | Whip / big jumps over 1-1.5 s with mild blur | `whip_s = 0.5`, `whip_blur_px = 14` | Try `whip_s = 1.2` and a smaller blur in the Geoverum profile |
| 5 | Zoom-through between scene types: ~4x accelerate out, radial blur (+ white flash), land 3-12x with power4.out over 2-3.5 s | `zoom_whip` capped at `whip_scale` 2.0 and `whip_in_s` 1.5 s; Geoverum uses hard cuts | Widen the `zoom_whip` bounds (or add `zoom_through`) and an optional white flash; use it on scene-type changes instead of hard cuts |
| 6 | One move zooms 20x (B, 35-40 s) | `max_zoom = 16` caps the deepest zoom relative to the whole map | Check whether journeys need a deeper cap (and the M02 map resolution it implies) |
| 7 | Camera moves median 0.64 fw/s, big jumps 2-3.5 fw/s | not measured per segment until P03 | Calibrate P03 `step_warn` and a peak-speed band from these numbers |

Items 1, 2, 4 and 6 are profile or authoring changes; 3 and 5 need engine code (a new ease in `layout.EASES` and the
template's `__legs`, wider transition bounds and a flash in `transitions.py`), with golden frames for each.

## Open questions for the owner

1. Should Geoverum Shorts become mainly one-map journeys (B), or keep several scenes joined by zoom-throughs (A)?
2. Drop the push-and-back pulses in favour of one-directional reframes?
3. White flash on zoom-throughs: yes or no?
