"""Gate result types."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Finding:
    gate: str            # e.g. "R07"
    level: str           # fail | fix | warn | info | skip | pass (skip: no input, or `not run: ...`)
    message: str
    seg: int | None = None
    frames: tuple[int, int] | None = None   # absolute half-open frame range the finding is about (style holds, gaps)
    waived: str | None = None               # why a style finding was lowered (gates/severity.py); None = not lowered

    def line(self) -> str:
        where = f" seg {self.seg}" if self.seg is not None else ""
        return f"[{self.level.upper():4}] {self.gate}{where}: {self.message}"


NOT_RUN = "not run: "   # message prefix of a `skip` finding whose check did not run at all (severity.not_checked)


def not_run(gate: str, why: str, seg: int | None = None) -> Finding:
    """A `skip` finding saying that the gate's check did not run (its stage was off or stopped early); reports list
    it under `not_checked` with kind "not_run"."""
    return Finding(gate, "skip", NOT_RUN + why, seg)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    subtimings: dict[str, dict] = field(default_factory=dict)   # stage -> sub-check seconds (layer2.dom_timing)
    fixes: list[dict] = field(default_factory=list)   # {"seg": i, "path": "camera_move.intensity", "from": a, "to": b}
    snapshots: dict[int, str] = field(default_factory=dict)
    dom: dict[int, dict] = field(default_factory=dict)                    # HyperFrames DOM probe results per segment
    caption_y_overrides: list[tuple] = field(default_factory=list)        # automatic caption placement (frames, y px)
    ai: dict = field(default_factory=dict)                                # --ai-qa: provider, model, retries, fallback

    def add(self, gate: str, level: str, message: str, seg: int | None = None) -> None:
        self.findings.append(Finding(gate, level, message, seg))

    def extend(self, fs: list[Finding]) -> None:
        self.findings.extend(fs)

    @property
    def failed(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failed

    def gates(self, level: str | None = None) -> set[str]:
        return {f.gate for f in self.findings if level is None or f.level == level}

    def timed(self, name: str):
        rep = self

        class _T:
            def __enter__(self):
                self.t = time.perf_counter()

            def __exit__(self, *a):
                rep.timings[name] = round(time.perf_counter() - self.t, 3)
        return _T()

    def summary(self) -> str:
        lines = [f.line() for f in self.findings if f.level != "pass"]
        n = {lv: sum(f.level == lv for f in self.findings) for lv in ("pass", "fix", "warn", "info", "skip", "fail")}
        t = " ".join(f"{k}={v:.2f}s" for k, v in self.timings.items())
        lines.append(f"preflight: {'OK' if self.ok else 'FAILED'} " + " ".join(f"{k}={v}" for k, v in n.items()) + f" | {t}")
        return "\n".join(lines)
