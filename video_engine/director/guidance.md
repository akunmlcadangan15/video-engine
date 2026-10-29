# Director Guidance

Adapted from the public `REMOTION.md` prompt guidance of [motif-dev/motif](https://github.com/motif-dev/motif)
(Copyright 2026 Motif, Apache License 2.0 — see `NOTICE` in this directory), reworked for this engine's locked-voiceover
documentary timeline and channel taste store, and enriched with Geoverum cartographic storytelling principles.

## STORYBOARD
PLANNING LADDER — work top-down:
1. Story arc: read the full narration first. Identify the hook (Geographical Paradox or Extreme Claim in Act I), the quantified reveal (Act II), the 3–5 escalating geographic stops or causal mechanisms (Act III), and the final twist/keyword callback (Act IV).
2. Concept directions: propose 3 genuinely different storyboard concepts over the same locked word sequence:
   - `grounded`: archival and documentary — let primary sources, maps, and clean labels carry the facts with quiet authority.
   - `contrast`: structure the beats around tensions, juxtapositions, and before/after or side-by-side comparisons (e.g., 80% vs 20% dividing line, Little Diomede vs Big Diomede, enclave vs mainland).
   - `bold`: open on the sharpest visual or geographical claim and drive pace through continuous multi-scale camera odysseys, blackout neighbor counters, dashed distance callouts, and decisive focal changes.
3. Beats: partition the narration into contiguous word spans (`from_word`..`to_word`, covering word 0 to the last word in order without gaps or overlaps). Each beat has one clear viewer goal and one compositional pattern from the pattern library (`establish`, `progressive_reveal`, `comparison`, `tracing`, `meaningful_repetition`, `emphasis_by_pause`, `paradox_hook`, `border_anomaly_tour`, `scale_overlay`, `causal_barrier`).
4. Geoverum Cartographic Discipline:
   - Never cut between two segments of the same map; fly continuously across stops (`zoom_out` -> pan -> deep `zoom_in`) so the viewer feels the real geographical distance and scale.
   - Pair every spoken statistic, distance, or border anomaly with a concrete visual anchor: a glowing dividing line, a dashed distance measurement (`km` / `mi`), a high-contrast pill stat badge, a flag badge, or a familiar scale overlay.
   - Keep one dominant focal point on screen at a time (e.g., land the hero percentage or year first, then reveal the supporting pill badge or leader-line label).
5. Restraint: every graphic element must earn its place by clarifying the spoken line. Do not invent dates, numbers, or place names that are not in the narration, script, asset notes, or credits. Prefer clear hierarchy and breathing room over decoration.

## REVIEW
VISUAL CRITIQUE — judge the sampled frames like a human creative director:
- Hierarchy and focal point: at every frame, is it immediately clear what the eye should look at first? Flag frames where a title, pin label, pill badge, and card compete at similar weight.
- Pacing and variety: does the visual change when the thought in the narration changes? Flag stretches where the frame looks static across consecutive samples or where too many elements fire at once.
- Map & spatial continuity: when moving between places on a map, does the camera glide smoothly along a continuous path with clear scale hierarchy (ALL CAPS country labels, Title Case feature labels with dot + leader line), rather than jumping with jarring cuts?
- Restraint and legibility: are labels, dates, routes, and overlays readable against the actual background pixels, clear of the bottom caption band (`y <= 1250` for captions) and top-right watermark, and free of clipped text or awkward overlaps?
- Grounding: does every on-screen text or graphic serve the spoken words at that timestamp without inventing unsupported facts?

Report only concrete, timestamped findings with evidence visible in the frames and a specific fix the draft stage can apply. Choose verdict `revise` only when there is a `blocker` or `major` finding that a timeline revision can fix; otherwise choose `pass`.
