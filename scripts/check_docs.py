#!/usr/bin/env python3
"""Checks the Markdown documentation of the repository. Exit 1 when something is wrong.

  - every relative link and anchor of a .md file resolves (external links are not fetched);
  - every document in docs/ is listed in docs/README.md;
  - docs/GATES.md embeds the current output of `engine gates --format md` (`--update` rewrites it);
  - every document under docs/ that a comment of the code, profiles or tests names exists.

Needs the package importable (`pip install -e .`) for the gate index; the other checks use the standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_FILES = [ROOT / n for n in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md", "AGENTS.md")]
MD_FILES += sorted((ROOT / "docs").rglob("*.md")) + sorted((ROOT / "examples").rglob("*.md"))
LINK = re.compile(r"(?<!\!)\[(?:[^\]]|\][^(])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
CODE_REF = re.compile(r"\bdocs/([A-Za-z0-9_./-]+\.md)\b")


def slug(text: str) -> str:
    """GitHub's anchor for a heading."""
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    t = re.sub(r"[`*]", "", t).strip().lower()
    return re.sub(r"[^\w\- ]", "", t).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out: set[str] = set()
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else HEADING.match(line)
        if m:
            s = slug(m.group(2))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else f"{s}-{n}")
    return out


def check_links(errors: list[str]) -> None:
    cache: dict[Path, set[str]] = {}
    for md in MD_FILES:
        fenced = False
        for no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE.match(line):
                fenced = not fenced
                continue
            if fenced:
                continue
            for target in LINK.findall(re.sub(r"`[^`]*`", "", line)):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target):      # http:, https:, mailto:
                    continue
                path_part, _, frag = target.partition("#")
                dest = md if not path_part else (md.parent / path_part).resolve()
                where = f"{md.relative_to(ROOT)}:{no}"
                if not dest.exists():
                    errors.append(f"{where}: link target {target!r} does not exist")
                    continue
                if frag and dest.suffix == ".md" and dest.is_file():
                    if dest not in cache:
                        cache[dest] = anchors(dest)
                    if frag.lower() not in cache[dest]:
                        errors.append(f"{where}: {target!r} has no heading with the anchor #{frag}")


def check_index(errors: list[str]) -> None:
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for doc in sorted((ROOT / "docs").glob("*.md")):
        if doc.name != "README.md" and f"]({doc.name}" not in index:
            errors.append(f"docs/README.md does not link to docs/{doc.name}")
    for folder in sorted(p for p in (ROOT / "docs").iterdir() if p.is_dir()):
        if f"]({folder.name}/" not in index:
            errors.append(f"docs/README.md does not link to the folder docs/{folder.name}/")


def check_gate_index(errors: list[str], update: bool) -> None:
    sys.path.insert(0, str(ROOT))
    try:
        from video_engine import gate_tools
    except Exception as e:                                   # noqa: BLE001  (a missing dependency is the usual cause)
        errors.append(f"cannot import video_engine to build the gate index ({type(e).__name__}: {e}); run `pip install -e .`")
        return
    path = ROOT / "docs" / "GATES.md"
    text = path.read_text(encoding="utf-8")
    if gate_tools.MD_BEGIN not in text or gate_tools.MD_END not in text:
        errors.append("docs/GATES.md lacks the generated-index markers")
        return
    a, b = text.index(gate_tools.MD_BEGIN), text.index(gate_tools.MD_END) + len(gate_tools.MD_END)
    fresh = gate_tools.render_markdown()
    if text[a:b] != fresh:
        if update:
            path.write_text(text[:a] + fresh + text[b:], encoding="utf-8")
            print("docs/GATES.md: gate index regenerated")
        else:
            errors.append("docs/GATES.md: the gate index is stale; run `python scripts/check_docs.py --update`")


def check_code_refs(errors: list[str]) -> None:
    for folder in ("video_engine", "profiles", "tests", "rust", "scripts"):
        for f in sorted((ROOT / folder).rglob("*")):
            if f.suffix not in {".py", ".toml", ".rs", ".pyi"} or "target" in f.parts or "__pycache__" in f.parts:
                continue
            if f.name in {"check_docs.py", "test_check_docs.py"}:      # they name made-up documents on purpose
                continue
            for no, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for ref in CODE_REF.findall(line):
                    if not (ROOT / "docs" / ref).exists():
                        errors.append(f"{f.relative_to(ROOT)}:{no}: names docs/{ref}, which does not exist")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="rewrite the generated gate index in docs/GATES.md")
    a = ap.parse_args()
    errors: list[str] = []
    check_links(errors)
    check_index(errors)
    check_gate_index(errors, a.update)
    check_code_refs(errors)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    print(f"checked {len(MD_FILES)} Markdown files: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
