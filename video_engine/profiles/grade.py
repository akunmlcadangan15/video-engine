"""Colour grading data contract, without rendering dependencies."""
from __future__ import annotations
import re
from typing import Optional, Union
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
LUMA = np.array([0.2126, 0.7152, 0.0722])
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

Chan = Union[float, tuple[float, float, float]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tint(_Strict):
    color: str                               # "#rrggbb": hue and chroma move toward it at the same luminance (clipped)
    amount: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _c(self):
        if not HEX.match(self.color):
            raise ValueError(f"tint color {self.color!r} must be #rrggbb")
        if float(_rgb(self.color) @ LUMA) < 0.05:
            raise ValueError(f"tint color {self.color} is too dark to carry a luminance (Rec. 709 luma < 0.05)")
        return self


class Grade(_Strict):
    lift: Chan = 0.0                         # -0.5..0.5: raises (or lowers) the blacks, whites stay
    gamma: Chan = 1.0                        # 0.2..5: > 1 brightens the mid-tones
    gain: Chan = 1.0                         # 0..4: scales the whites
    saturation: float = Field(1.0, ge=0, le=3)
    tint: Optional[Tint] = None
    lut: Optional[str] = None                # .cube 3D LUT after the values above (segment / beat: project-relative)
    strength: float = Field(1.0, ge=0, le=1)  # mix of the graded picture over the ungraded one

    @model_validator(mode="after")
    def _r(self):
        for name, lo, hi in (("lift", -0.5, 0.5), ("gamma", 0.2, 5.0), ("gain", 0.0, 4.0)):
            for v in np.atleast_1d(getattr(self, name)):
                if not lo <= v <= hi:
                    raise ValueError(f"grade {name} {v} outside {lo}..{hi}")
        if self.lut is not None and not self.lut.lower().endswith(".cube"):
            raise ValueError(f"grade lut {self.lut!r} must be a .cube file")
        return self

    @property
    def identity(self) -> bool:
        return self.strength == 0 or (self == Grade(strength=self.strength))


def _rgb(hex_: str) -> np.ndarray:
    return np.array([int(hex_[k:k + 2], 16) for k in (1, 3, 5)]) / 255.0


