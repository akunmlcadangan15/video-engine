# Geoverum Creative Direction Specification

The engine loads all `- ` bullet lines in this document as `[draft] rules` via `rules_file = "geoverum/creative.md"`. Keep channel identity, default staging behaviors, and storyboard alternatives explicit so any third-party engineer, director, or AI agent can interpret them without ambiguity. Defaults serve as baseline staging principles rather than rigid blocking validators; any intentional creative departure must be documented in the segment's `intent` (and `intent.reason`) field. Factual accuracy, licensed asset provenance, and render integrity are mandatory and never waived by creative intent.

## Identity

- Identity: explain geography, borders, climate, hydrology, and history with high-contrast cartographic precision strictly grounded in the supplied narration, script, and verified evidence; never invent place names, dates, quantities, or spatial scales, and never alter locked voiceover timing.
- Identity: open within the first 1.5 seconds on an already-moving dark satellite basemap featuring an immediate visual paradox or spatial contradiction (such as a glowing latitude/border line, crossed-out bridge markers along a major river, a comparative title equation whose equals sign is struck through into `≠`, or an active hazard zone) and terminate abruptly on the final factual punchline without any call-to-action or subscribe screen.

## Defaults

- Default, not a mandate: favor a continuous multi-stop camera trajectory (`map_path` using `flow`, `glide`, `reveal`, `settle`, or `whip` moves combined with 2.5D `tilt` and `roll` banking) across a single dark satellite basemap so the viewport remains in motion across 96–100% of frames (`max_hold <= 0.9 s`), cutting away to full-screen non-map scenes only for dedicated evidence beats (such as 2D/3D geological cross-sections, archival treaties and parchment maps, or real-world documentary footage).
- Default, not a mandate: anchor every spoken number, distance, temperature, percentage, or historical epoch on screen within 1.5–2.5 seconds of its spoken cue using a dedicated visual actor—specifically counting `map_figures`, double-arrow geodesic measurement bars with dual `km`/`mi` units, animated `map_thermometers`, dark charcoal banner pills (`#18181c`), 45-degree diagonal `hatch` fills for contested or submerged zones, and white-bordered cutout props, maritime vessels, moving flag badges, or concise comic `map_bubbles`.

## Alternatives

- Alternatives: generate three structurally distinct storyboard concepts over the same locked voiceover: `grounded` (continuous satellite fly-through punctuated by archival document, framed photo inset, and documentary footage evidence beats); `contrast` (split-screen or tilted-latitude comparison, binary policy-zone color division, and side-by-side thermometer or stat-pill juxtaposition); and `bold` (an unbroken macro-continent-to-16x-micro-enclave camera journey or a flat-projection-to-3D-globe great-circle reveal).
- Intent: document the narrative purpose of every shot and overlay. Render burned-in captions as single-line phrases (`max_words = 3`, Inter Bold 84 px white with a soft drop shadow and no backing box by default) anchored in the lower placement zone (`fixed_y = 1417`), permit planned visual overlap (`reference_style.overlap.allowed = true`) whenever foreground-to-background text contrast remains legible, and keep `creative_autofix` disabled unless explicitly requested.
