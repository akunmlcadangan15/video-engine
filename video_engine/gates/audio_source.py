"""A02 audio source gate: with profile `[audio] require_source`, every music and SFX file of the mix comes from
that source (provenance `source`); a file from any other source (local-test tones, project bgm_path) fails."""
from __future__ import annotations

from .. import profiles
from .core import Finding

GATE = "A02"


def sources(tl, mix) -> list[tuple[str, dict]]:
    """(label, provenance) of every file the final audio uses: the mix's music and SFX files (mixer.Mix), or for a
    profile without [sound] (mix None) the project's audio_chain.bgm_path with an unknown source."""
    if mix is None:
        bgm = tl.audio_chain.bgm_path
        return [("music " + bgm, {"source": "project-file", "id": tl.audio_chain.bgm_asset_id or bgm})] if bgm else []
    out = [("music", mix.music.provenance)] if mix.music else []
    kind = {}
    for c in mix.cues:
        kind.setdefault(c.asset_id, c.kind)
    return out + [(f"sfx {k}", mix.sounds[a].provenance) for a, k in kind.items()]


def a02_audio_source(tl, mix, require: str | None = None) -> list[Finding]:
    """`require` defaults to the timeline profile's [audio] require_source; None = skip."""
    req = require if require is not None else profiles.of(tl).audio.require_source
    if req is None:
        return [Finding(GATE, "skip", "profile requires no audio source")]
    items = sources(tl, mix)
    bad = [f"{label} {p.get('id')!r} source {p.get('source')!r}" for label, p in items if p.get("source") != req]
    if bad:
        return [Finding(GATE, "fail", f"{len(bad)} of {len(items)} audio files not from '{req}': " + "; ".join(bad))]
    return [Finding(GATE, "pass", f"{len(items)} audio files from '{req}'")]


def build_findings(tl, mix) -> list[Finding]:
    """A02 as a delivery gate of the build: no finding when the profile requires no source (like S01 without its
    table), else a02_audio_source."""
    return [] if profiles.of(tl).audio.require_source is None else a02_audio_source(tl, mix)
