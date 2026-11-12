"""A02 audio source gate (preferred, not mandatory by default): with profile `[audio] require_source`, every music and
SFX file of the mix should come from that source (provenance `source`); a file from any other source (local-test tones,
project bgm_path) emits a warning unless A02 is explicitly required in `gates.require`."""
from __future__ import annotations

from pathlib import Path

from .. import profiles
from .core import Finding

GATE = "A02"


def sources(tl, mix, project_dir=None) -> list[tuple[str, dict]]:
    """(label, provenance) of every file the final audio uses: the mix's music and SFX files (mixer.Mix), or for a
    profile without [sound] (mix None) the project's audio_chain.bgm_path. A path file whose sidecar or sound lock
    verifies source=epidemic, a track id and the file hash uses that provenance; otherwise the source is
    project-file."""
    if mix is None:
        bgm = tl.audio_chain.bgm_path
        if not bgm:
            return []
        prov = {"source": "project-file", "id": tl.audio_chain.bgm_asset_id or bgm}
        if project_dir is not None:
            path = Path(project_dir) / bgm
            if path.is_file():
                from ..sound import provenance
                found = provenance.for_file(path, Path(project_dir))
                if found:
                    prov = found
        return [("music " + bgm, prov)]
    out = [("music", mix.music.provenance)] if mix.music else []
    kind: dict[str, str] = {}
    for c in mix.cues:
        kind.setdefault(c.asset_id, c.kind)
    return out + [(f"sfx {k}", mix.sounds[a].provenance) for a, k in kind.items()]


def a02_audio_source(tl, mix, require: str | None = None, project_dir=None) -> list[Finding]:
    """`require` defaults to the timeline profile's [audio] require_source; None = skip. Mismatch emits a warning
    by default and blocks only when A02 is listed in `gates.require`."""
    req = require if require is not None else profiles.of(tl).audio.require_source
    if req is None:
        return [Finding(GATE, "skip", "profile requires no audio source")]
    items = sources(tl, mix, project_dir)
    bad = [f"{label} {p.get('id')!r} source {p.get('source')!r}" for label, p in items if p.get("source") != req]
    if bad:
        return [Finding(GATE, "warn", f"{len(bad)} of {len(items)} audio files not from preferred '{req}' "
                                       f"(preferred, not required by default): " + "; ".join(bad))]
    return [Finding(GATE, "pass", f"{len(items)} audio files from '{req}'")]


def build_findings(tl, mix, project_dir=None) -> list[Finding]:
    """A02 as a delivery gate of the build: no finding when the profile sets no preferred source (like S01 without
    its table), else a02_audio_source (warns by default unless A02 is in `gates.require`)."""
    if profiles.of(tl).audio.require_source is None:
        return []
    return a02_audio_source(tl, mix, project_dir=project_dir)
