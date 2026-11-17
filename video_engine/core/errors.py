"""Errors callers can report without a traceback."""


class EngineError(RuntimeError):
    """An actionable engine failure."""
    tried: list[tuple[str, str]]
