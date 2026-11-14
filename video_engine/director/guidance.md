# Director Guidance

Adapted from the public `REMOTION.md` prompt guidance of [motif-dev/motif](https://github.com/motif-dev/motif)
(Copyright 2026 Motif, Apache License 2.0) and the working principles of the stageshow skill
([benjitaylor/skills](https://github.com/benjitaylor/skills), MIT, Copyright (c) 2026 Benji Taylor), reworded for this
engine's locked-voiceover documentary timeline and channel taste store. Attribution: `NOTICE` in this directory.

Global principles only: channel visual style and technical legibility live in the channel profile (brand profiles leave directing and duration open unless optional `[draft] rules` / `rules_file` are configured); historical preferences live in the taste store. Do not turn these preferences into new technical constraints.
Each `## SECTION` is prompt text (`director.guidance`): STORYBOARD for the storyboard request, EDIT for the timeline draft, REVIEW for the frame review. Every request pays for it, so keep it short.

## STORYBOARD
PLANNING LADDER. The idea: each moment shows this subject's own evidence, one thing at a time, and every change on screen has a cause the viewer hears or sees. Most of the quality is decided here, before anything is built; the engine's checks catch mistakes, not dull ideas.
1. Read the full narration. Find the question, evidence and payoff this subject actually supports. Preserve word order and facts; no fixed act count, hook type or keyword callback is required.
2. Propose 3 genuinely different films over the same locked words:
   - `grounded`: documentary or footage-led evidence, restrained labels, purposeful stills and reading holds.
   - `contrast`: comparisons and juxtaposition that make a causal or spatial distinction clear.
   - `bold`: a minimalist reveal, a scale demonstration or a purposeful camera journey, not an obligation to animate everything.
   Different colours and transitions alone do not make different concepts.
3. Partition the narration into contiguous word spans (every word once, in order). Plan each span before its look: the goal, the focal (the one thing the eye lands on), the cause of its change and a pattern; for the concept, the payoff (`hero_moment`) and what is left out. Timeline Beat is a different unit, see docs/GLOSSARY.md.
4. How generated documentary video fails, and the cure:
   - Too much: pins, labels, arrows and numbers everywhere. Few real words, each said once; at most one number shown large.
   - Bare: a map with a dot and a title, a stock still under a slow zoom. Show the subject's own evidence.
   - Boxed: the evidence shrunk into a card, a whole map shown small while the story sits in one valley. Frame where it happens.
   - Flat: title, pins and labels at one weight, so nothing leads. Give the most weight to what the narration is about.
   - Bland: the shot any video of this category gets (a spinning globe, a zoom to a dot). Ask what only this subject can show.
5. Keep map geography, scales and comparisons honest; never invent dates, numbers or place names absent from narration, script, asset notes or credits. A continuous path keeps orientation, a cut marks a distinction; neither is required. Negative space, static shots and an ending without a callback are valid when their purpose is stated.

## EDIT
CRAFT. Build the plan; do not decorate it.
- Each segment has one focal thing, placed where the eye lands and given the heaviest mark; the rest stays quiet.
- Every entrance, camera move and text change has a cause: start it at the word that names it or the shown event that triggers it. Nothing appears without a reason to.
- Few real words on screen, each said once; at most one number shown large.
- The payoff holds long enough to read. A held still is a choice, not a gap.

## REVIEW
FRESH-EYE CRITIQUE. You did not build this video and get none of the builder's reasons: judge the frames against the narration and the brief. A declared intent (motion, negative space, hold: read) is the builder's claim, not a verdict: check it against the frame.
First ask: does this answer THIS subject, or could it be any video of its category? If any, that is a framing finding.
Then look in this order, largest fault first:
- framing: the focal thing where the eye goes, at a size that reads; places, scales and comparisons clear. A cut on the same map is fine when it helps.
- hierarchy: one heaviest mark, on the evidence; flag competing information only when it hides the meaning.
- motion: each change follows a spoken word or a shown event, nothing pops or sits half drawn, the payoff holds long enough to read. A static shot, quiet opening, negative space or reading hold is not itself a defect; tell intended stillness from a failed animation.
- legibility: key information clipped or unreadable, judged against the effective safe areas and caption placement, never invented coordinates.
- grounding: the visible evidence supports the narration, with no unsupported facts.
Every finding stands on a frame you were sent: cite its t_s, describe what it shows as evidence, give one specific fix. Order by the harm to the viewer's understanding, not by the effort of the fix. List in `not_checked` what these frames cannot show. Declare `blocker` for broken output or an unsupported fact, `major` for a clear comprehension failure, `minor` for taste. Choose `revise` only when a blocker or major finding can be repaired, never because a preferred style is absent.
