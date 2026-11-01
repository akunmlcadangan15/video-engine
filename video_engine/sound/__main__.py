"""`python -m video_engine.sound PROJECT [--resume] [--credits]`: ensure_sounds for the project, then list the files
the mixer resolves (slot, source, id, title, artist, cache path). --credits appends their credit lines to
<project>/credits.txt. Exit codes: 0 done, 3 request bundle written (answer it, rerun with --resume), 1 error."""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .. import timeline
from ..ai.base import AwaitingAgent
from ..util import EngineError
from . import cache, request

EXIT_AWAITING = 3


def main(argv: list[str] | None = None) -> int:
    from pydantic import ValidationError
    ap = argparse.ArgumentParser(prog="python -m video_engine.sound")
    ap.add_argument("project")
    ap.add_argument("--resume", action="store_true", help="import response.json of the pending sound request")
    ap.add_argument("--credits", action="store_true", help="append credit lines to <project>/credits.txt")
    argv = sys.argv[1:] if argv is None else list(argv)
    a = ap.parse_args(argv)
    p = Path(a.project).resolve()
    resume_cmd = shlex.join(["python", "-m", "video_engine.sound", *[x for x in argv if x != "--resume"], "--resume"])
    try:
        tl = timeline.load(p / "master_timeline.json")
        for r in request.ensure_sounds(tl, p, resume=a.resume, resume_cmd=resume_cmd):
            print(f"imported: {r.provenance['source']} {r.provenance['id']} \"{r.provenance['title']}\" -> {r.path}")
        got = request.resolved(tl, p)
        for r in got:
            pv = r.provenance
            print(f"resolved: {pv['source']} {pv['id']} \"{pv['title']}\" ({pv['artist']}) {r.path}")
        if a.credits:
            for line in cache.append_credits(p, [r.provenance for r in got]):
                print(f"credit: {line}")
    except AwaitingAgent as e:
        print(f"waiting for agent response: {e.bundle}\n  answer: {e.bundle / 'response.json'} "
              f"(schema: {e.bundle / 'schema.json'})\n  resume: {e.resume}")
        return EXIT_AWAITING
    except (EngineError, ValidationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
