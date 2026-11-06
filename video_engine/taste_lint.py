"""Taste lint: checks that a channel's taste store and its profile say the same thing.

A taste entry (`liked` / `rejected`, free text with a reason) may carry a `check`: one condition, or a list of them,
that the channel PROFILE has to satisfy for the entry to be true. For a liked entry the check is what the profile
does; for a rejected entry it is what keeps the rejected thing out (for example `transitions.page_turn == false`).

    {"key": "caption_style.size", "op": "==", "value": 84}

`key` is a dotted path into the merged profile (dict keys and list indexes). Operators: == != < <= > >= in exists
(`exists` takes true or false). Entries without a `check`, and the free-text `rules`, cannot be checked by a program;
they are only counted. This module changes nothing."""
from __future__ import annotations

import json
import operator
import re
from typing import Any

OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
       ">=": operator.ge, "in": lambda actual, wanted: actual in wanted}
CHECK_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_.\[\]-]+)\s+(?P<op>==|!=|<=|>=|<|>|in|exists)\s+(?P<value>.+?)\s*$")
MISSING = object()


class CheckError(ValueError):
    pass


def parse_check(text: str) -> dict:
    """`"caption_style.size == 84"` -> {"key", "op", "value"}; the value is JSON (`84`, `true`, `[84]`, `"epidemic"`)."""
    m = CHECK_RE.match(text)
    if not m:
        raise CheckError(f"check {text!r} must read KEY OP VALUE, for example `caption_style.size == 84` "
                         f"(operators: {', '.join([*OPS, 'exists'])}; VALUE is JSON)")
    try:
        value = json.loads(m["value"])
    except ValueError:
        raise CheckError(f"check {text!r}: the value {m['value']!r} is not JSON (write strings in double quotes)") from None
    return validate({"key": m["key"], "op": m["op"], "value": value})


def validate(c: Any) -> dict:
    """The check as a clean dict, or CheckError."""
    if not isinstance(c, dict) or set(c) - {"key", "op", "value"} or not {"key", "op", "value"} <= set(c):
        raise CheckError(f"a check is an object with key, op and value, got {c!r}")
    if not isinstance(c["key"], str) or not c["key"]:
        raise CheckError(f"check key must be a non-empty string, got {c['key']!r}")
    if c["op"] not in (*OPS, "exists"):
        raise CheckError(f"check operator {c['op']!r} unknown (known: {', '.join([*OPS, 'exists'])})")
    if c["op"] == "exists" and not isinstance(c["value"], bool):
        raise CheckError("`exists` takes true or false")
    if c["op"] == "in" and not isinstance(c["value"], list):
        raise CheckError("`in` takes a list")
    return {"key": c["key"], "op": c["op"], "value": c["value"]}


def checks_of(entry: dict) -> list[dict]:
    """The entry's checks as a list (none when it has no `check`)."""
    c = entry.get("check")
    if c is None:
        return []
    return [validate(x) for x in (c if isinstance(c, list) else [c])]


def lookup(data: Any, key: str) -> Any:
    """Value at the dotted `key` of nested dicts and lists, or MISSING."""
    cur = data
    for part in re.findall(r"[^.\[\]]+", key):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return MISSING
    return cur


def evaluate(check: dict, data: dict) -> tuple[str, Any]:
    """(status, actual): ok | mismatch | unknown-key. `exists` never reports unknown-key."""
    actual = lookup(data, check["key"])
    if check["op"] == "exists":
        present = actual is not MISSING and actual is not None
        return ("ok" if present == check["value"] else "mismatch"), (None if actual is MISSING else actual)
    if actual is MISSING or actual is None:
        return "unknown-key", None
    try:
        return ("ok" if OPS[check["op"]](actual, check["value"]) else "mismatch"), actual
    except TypeError:
        return "mismatch", actual


def lint(taste: dict, profile_data: dict) -> dict:
    """{"results": [{list, what, key, op, value, actual, status}], "unchecked": [what, ...], "rules": n}.
    profile_data is the merged profile as plain data (json.loads(profile.data.model_dump_json()))."""
    results, unchecked = [], []
    for lst in ("liked", "rejected"):
        for e in taste.get(lst, []):
            try:
                checks = checks_of(e)
            except CheckError as err:
                results.append({"list": lst, "what": e.get("what", "?"), "key": "", "op": "", "value": None,
                                "actual": str(err), "status": "bad-check"})
                continue
            if not checks:
                unchecked.append(e.get("what", "?"))
            for c in checks:
                status, actual = evaluate(c, profile_data)
                results.append({"list": lst, "what": e.get("what", "?"), **c, "actual": actual, "status": status})
    return {"results": results, "unchecked": unchecked, "rules": len(taste.get("rules", []))}


def ok(report: dict) -> bool:
    return all(r["status"] == "ok" for r in report["results"])


def render(report: dict, channel: str, profile: str) -> str:
    rs = report["results"]
    lines = [f"taste lint: store '{channel}' against profile '{profile}'"]
    last = None
    for r in rs:
        if (r["list"], r["what"]) != last:
            last = (r["list"], r["what"])
            lines.append(f"  [{r['list']}] {r['what']}")
        mark = {"ok": "ok      ", "mismatch": "MISMATCH", "unknown-key": "NO KEY  ", "bad-check": "BAD CHECK"}[r["status"]]
        if r["status"] == "bad-check":
            lines.append(f"      {mark} {r['actual']}")
            continue
        want = f"{r['key']} {r['op']} {json.dumps(r['value'])}"
        got = "" if r["status"] == "ok" else (" (profile has nothing there)" if r["status"] == "unknown-key"
                                              else f" (profile has {json.dumps(r['actual'])})")
        lines.append(f"      {mark} {want}{got}")
    bad = sum(r["status"] != "ok" for r in rs)
    tail = f"{len(rs) - bad} of {len(rs)} checks ok; {len(report['unchecked'])} entries without a check"
    if report["rules"]:
        tail += f" and {report['rules']} free-text rules cannot be checked by a program"
    lines.append(tail)
    return "\n".join(lines)
