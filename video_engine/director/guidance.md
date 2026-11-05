# Director Guidance

Adapted from the public `REMOTION.md` prompt guidance of [motif-dev/motif](https://github.com/motif-dev/motif)
(Copyright 2026 Motif, Apache License 2.0 — see `NOTICE` in this directory), reworked for this engine's locked-voiceover documentary timeline and channel taste store.

Global principles only: channel identity, defaults and alternatives live in `profiles/<channel>/creative.md` (loaded as `[draft] rules`); historical preferences live in the taste store. Do not turn these preferences into new technical constraints.

## STORYBOARD
PLANNING LADDER — work top-down:
1. Read the full narration. Find the question, evidence and payoff that this subject actually supports. Preserve word order and facts; no fixed act count, hook type or keyword callback is required.
2. Propose 3 genuinely different films over the same locked words:
   - `grounded`: documentary or footage-led evidence, restrained labels, purposeful stills and reading holds.
   - `contrast`: comparisons and juxtaposition that make a causal or spatial distinction clear.
   - `bold`: a minimalist reveal, a scale demonstration or a purposeful camera journey, not an obligation to animate everything.
   Different colours and transitions alone do not make different concepts.
3. Partition narration into contiguous word spans (`from_word`..`to_word`, covering every word in order without gaps or overlaps). Each span has one viewer goal and a suitable pattern from the supplied pattern library. Timeline Beat is a different unit, see docs/GLOSSARY.md.
4. Let visual evidence drive composition. Keep map geography, comparison scales and labels honest. A continuous path can preserve spatial orientation; a cut can make a narrative distinction. Neither is universally required. Choose the appropriate surface and source for the subject, not a channel-wide shot template.
5. Restraint: do not invent dates, numbers or place names absent from narration, script, asset notes or credits. Every element should clarify the spoken idea. Negative space, static shots and an ending without a callback are valid choices; explain their purpose.

## REVIEW
VISUAL CRITIQUE — judge the frames against the shot goal and declared intent, not an animation quota:
- Hierarchy: can the viewer identify the important evidence? Flag competing information only when it obscures meaning.
- Pacing: does the viewer have time to understand the idea? A static shot, quiet opening, negative space or reading hold is not itself a defect; distinguish intentional stillness from a failed expected animation.
- Spatial continuity: are places, scales and comparisons clear? A cut on the same map is acceptable when it helps the story.
- Legibility: flag clipped or unreadable key information, not decorative overlap or breathing room by itself. Respect the effective timeline/profile safe areas and caption placement; do not invent fixed coordinates.
- Grounding: does the visible evidence support the narration without invented facts?

Report only concrete, timestamped findings with visible evidence and a specific fix. Declare `blocker` for broken output or unsupported factual content, `major` for a clear comprehension failure, and `minor` for taste suggestions. Choose `revise` only when a blocker or major finding can be repaired; do not request a revision solely because a preferred aesthetic pattern is absent.
