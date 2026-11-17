"""Shared scene text formatting."""
import math

def figure_text(f, v: float, theme: dict) -> str:
    """On-screen text of figure f showing value v: years as plain digits; numbers rounded half up to f.decimals with
    the theme separators (same rule as the template counter)."""
    if f.kind == "year":
        return f"{f.prefix}{int(v)}{f.suffix}"
    m = 10 ** f.decimals
    n = math.floor(abs(v) * m + 0.5)
    whole = f"{n // m:,}".replace(",", theme["number_group"])
    frac = f"{theme['number_decimal']}{n % m:0{f.decimals}d}" if f.decimals else ""
    return f"{f.prefix}{'-' if v < 0 and n else ''}{whole}{frac}{f.suffix}"

