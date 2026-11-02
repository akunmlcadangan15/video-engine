# Proposal: other frame sizes and fractional frame rates

Status: audit only, nothing built.
Reason: other Hypoverum channels may need a format other than 1080×1920 at 30 fps.
No such channel is defined yet, so this document records what is known and what must be measured first.

**What this document answers**

- Which parts of the engine already follow `settings.width`, `settings.height` and `settings.fps`.
- Which parts are tied to 1080×1920 or to whole frame rates.
- What a change to fractional frame rates would touch, and in which order to do it.

## What the code already does (confirmed in the source)

- `Settings.width` and `Settings.height` are free even integers (default 1080 and 1920); `Settings.fps` is an integer from 1 to 99 (`video_engine/timeline.py`).
- Gates express pixel lengths at a **1080×1920 reference frame** and scale them: the safe area (`SAFE_X`, `SAFE_TOP`, `SAFE_BOTTOM` in `gates/layer2.py` and `watermark.py`), the R56 minimum motion (scaled by the frame diagonal), caption clearance and the watermark size all multiply by `W / 1080` or `H / 1920`.
- Profile lengths are documented as pixels at 1080×1920 (`[print]`, `[layout]`, `[watermark]`, `fixed_y`, `max_bottom_y`, ...).
- The Rust crate takes width, height and fps as arguments (`compile_ass`, `camera_*`, `warp_frame`); it limits one side to 32768 px and fps to 1 through 99.

## What is tied to 1080×1920 or to whole frame rates

- The literals 1080 and 1920 appear in 14 Python files and 3 Rust files, mostly as the reference frame above.
  Which of them are *not* scaled is **unconfirmed**; a search for the two numbers lists the candidates.
- The Geoverum profile is a vertical-Shorts profile: caption `fixed_y` 1382, `max_bottom_y` 1480 (YouTube Shorts UI), the layout zones Z1 and Z2, the grid of 6 columns, and the safe area of the profile `[layout]` are absolute positions for 1080×1920.
  They do not scale with `settings`.
- A frame number is an integer everywhere, and `frame_to_cs(frame, fps) = frame * 100 / fps` is integer-only (docs/RUST.md, Frame and time rules).
  At `fps >= 100` two frames would share a centisecond in `.ass` time; fractional rates (29.97) cannot be represented.
- No test renders a size other than 1080×1920: **unconfirmed** whether HyperFrames templates, the beats layout planner and the native renderer behave at, for example, 1920×1080.

## Options

1. **Another vertical size at an integer frame rate** (for example 720×1280 or 1080×1350 at 25 or 30 fps).
   Probably works today for the native path and the gates that scale; needs a real build to find what does not.
   Cost: low. Do this first, as an experiment.
2. **A horizontal format (1920×1080).**
   Needs a new channel profile with its own safe zones and layout (the Shorts constants do not apply), and a check of the beats layout planner.
   Cost: medium, mostly profile work.
3. **Fractional frame rates (29.97, 59.94).**
   Needs a rational time base instead of the integer `fps`: the schema, `sec_to_frame`, `frame_to_cs`, `compile_ass`, the pause detector, HyperFrames timing, stream-copy checks (`r_frame_rate`) and every cache key that holds `fps`.
   Cost: high, and it changes determinism.
   Do it only when a delivery target requires it; most platforms accept 30 fps.

## Recommendation

1. Define the channel and its format first (platform, size, frame rate, safe zones).
2. Build `examples/minimal` at that size with the default profile and read the gate output; the failures list the real gaps.
3. Write the channel profile; move any constant that should not scale into the profile.
4. Treat fractional frame rates as a separate project, started only by a concrete delivery requirement.

## Questions for the owner

- Which channel, platform and format is the next one?
- Does any delivery target reject 30 fps?
