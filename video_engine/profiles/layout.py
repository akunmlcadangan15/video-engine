"""Pure layout configuration contracts."""
from __future__ import annotations
from typing import TYPE_CHECKING, Annotated, Any, Optional
from pydantic import BaseModel, ConfigDict, Field, create_model, model_serializer, model_validator

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetOnlyModel(BaseModel):
    """Strict model that serializes only explicitly set fields (`exclude_unset` behavior in nested dumps)."""
    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def _serialize_set_only(self, handler) -> dict[str, Any]:
        d = handler(self)
        keep = self.model_fields_set | set((self.model_extra or {}).keys())
        return {k: v for k, v in d.items() if k in keep}


def partial_model(name: str, base_cls: type[BaseModel], *, frozen: bool = False) -> type[SetOnlyModel]:
    """Create a strict partial-override Pydantic model from `base_cls` with every field optional."""
    cfg = ConfigDict(extra="forbid", frozen=frozen)
    fields: dict[str, Any] = {}
    for fname, info in base_cls.model_fields.items():
        if fname in ("portrait", "landscape"):
            continue
        ann = Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
        fields[fname] = (Optional[ann], None)
    return create_model(name, __base__=SetOnlyModel, __config__=cfg, __module__=base_cls.__module__, **fields)


class LayoutGrid(_M):
    x0: float = 64                 # first column left edge
    columns: int = Field(6, ge=1)
    col_w: float = 104
    gutter: float = 40
    unit: int = Field(8, ge=1)     # spacing unit: positions and gaps are multiples
    snap_tol: float = 2            # column snap tolerance px
    unit_tol: float = 1            # multiple-of-unit tolerance px

    def edges(self) -> list[float]:
        return [self.x0 + i * (self.col_w + self.gutter) for i in range(self.columns)]


class LayoutZones(_M):
    safe: tuple[float, float, float, float] = (48, 288, 888, 1248)   # text safe area x0, y0, x1, y1
    z1: tuple[float, float, float, float] = (64, 304, 888, 440)      # top slot: title or data card
    z2: tuple[float, float, float, float] = (64, 488, 888, 1100)     # map focus: active region, pins, labels, routes
    z2_pad: float = 48             # camera fit padding inside Z2
    z3: tuple[float, float, float, float] | None = None  # optional reference zone; not read by the planner or gates
    group_gap: float = 48          # min gap between zones


class LayoutSizes(_M):
    title: float = 112
    year: float = 128
    time: float = 56
    caption: float = 72            # informational: the caption size comes from caption_style
    pin_label: float = 52
    pin_label_min: float = 44
    pin_label_step: float = 4      # size fallback step
    area_label: float = 44
    area_tracking: float = 0.08    # em
    hero_ratio: float = 1.5        # hero >= ratio x next-largest text (caption excluded)
    big_number: float = 100        # numbers at or above this size are big


class LayoutBudget(_M):
    texts: int = 4                 # visible texts per frame incl. caption and fading ones, watermark excluded
    labelled_pins: int = 2
    routes: int = 1
    animating: int = Field(2, gt=0)   # elements entering, exiting, drawing, counting or moving at once
    big_numbers: int = 1
    saturated_regions: int = 1


class LayoutTiming(_M):
    enter_s: float = 0.3
    exit_s: float = 0.25
    stagger_s: float = 0.2
    stagger_min_s: float = 0.15
    stagger_max_s: float = 0.25
    exit_min_s: float = 0.2
    exit_max_s: float = 0.3
    settle_s: float = 0.8          # camera settle per beat
    head_drop_s: float = 0.6       # teardrop head entry (bounce)
    min_hold_s: float = 1.0        # hold >= max(min_hold_s, chars / chars_per_s)
    chars_per_s: float = 20
    move_fade_s: float = 0.3       # label move: fade out + in
    move_gain: float = 0.3         # non-urgent label move: min relative cost gain
    move_hold_s: float = 0.2       # ... held this long
    move_gap_s: float = 0.5        # never two label moves within this time
    moves_per_shot: int = 1        # non-urgent label moves per shot
    open_zoom_out: float = Field(1.35, ge=1.0)   # first key: the first beat's fit zoomed out by this factor


class LayoutLabels(_M):
    offset: float = 16             # label box to pin head
    edge_clear: float = 24         # label box to any region edge
    text_clear: float = 16         # label box to other text, heads, zones
    route_clear: float = 24        # route to any text
    area_inset: float = 24         # area label box inside its polygon
    leader_max: float = 120
    leader_px: float = 2
    route_pin_gap: float = 24      # route end to its to_pin head
    card_gap: float = 32           # data card: time ink starts this far right of the year ink
    dim_alpha: float = 0.10        # fill opacity of inactive regions
    caption_ground: float = 0.10   # max fraction of the caption box over a highlighted fill
    order: tuple[str, ...] = ("TR", "BR", "TL", "BL", "TC", "BC", "R", "L")


class LayoutFly(_M):
    """Profile [layout.fly]: managed texts enter and exit by scaling through the frame (template layout block).

    Scale runs over the span's enter / exit time; opacity runs over `<kind>_opacity_in_s` on entry (span time
    when unset) and with the scale on exit."""
    title_in: float = Field(2.6, ge=1.0)            # title enters at this scale
    figure_in: float = Field(2.2, ge=1.0)           # data card figures (year) enter at this scale
    label_in: float = Field(3.0, ge=1.0)            # pin and area labels enter at this scale
    out: float = Field(2.6, ge=1.0)                 # every managed text exits to this scale
    title_opacity_in_s: float | None = Field(0.15, gt=0)
    figure_opacity_in_s: float | None = Field(0.40, gt=0)
    label_opacity_in_s: float | None = Field(None, gt=0)
    ease_in: str = "expo.out"                       # GSAP ease names
    ease_out: str = "power2.in"                     # cubic ease-in


if TYPE_CHECKING:
    class LayoutGridOverride(SetOnlyModel): pass
    class LayoutZonesOverride(SetOnlyModel): pass
    class LayoutSizesOverride(SetOnlyModel): pass
    class LayoutBudgetOverride(SetOnlyModel): pass
    class LayoutTimingOverride(SetOnlyModel): pass
    class LayoutLabelsOverride(SetOnlyModel): pass
    class LayoutFlyOverride(SetOnlyModel): pass
else:
    LayoutGridOverride = partial_model("LayoutGridOverride", LayoutGrid, frozen=True)
    LayoutZonesOverride = partial_model("LayoutZonesOverride", LayoutZones, frozen=True)
    LayoutSizesOverride = partial_model("LayoutSizesOverride", LayoutSizes, frozen=True)
    LayoutBudgetOverride = partial_model("LayoutBudgetOverride", LayoutBudget, frozen=True)
    LayoutTimingOverride = partial_model("LayoutTimingOverride", LayoutTiming, frozen=True)
    LayoutLabelsOverride = partial_model("LayoutLabelsOverride", LayoutLabels, frozen=True)
    LayoutFlyOverride = partial_model("LayoutFlyOverride", LayoutFly, frozen=True)


class LayoutBranch(SetOnlyModel):
    """Orientation-specific overrides under `[layout.portrait]` / `[layout.landscape]`."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    grid: LayoutGridOverride | None = None
    zones: LayoutZonesOverride | None = None
    sizes: LayoutSizesOverride | None = None
    budget: LayoutBudgetOverride | None = None
    timing: LayoutTimingOverride | None = None
    labels: LayoutLabelsOverride | None = None
    fly: LayoutFlyOverride | None = None


class LayoutProfile(_M):
    """Profile [layout] table. Its presence turns on the beats layout and the L gates."""
    grid: LayoutGrid = LayoutGrid()
    zones: LayoutZones = LayoutZones()
    sizes: LayoutSizes = LayoutSizes()
    budget: LayoutBudget = LayoutBudget()
    timing: LayoutTiming = LayoutTiming()
    labels: LayoutLabels = LayoutLabels()
    fly: LayoutFly | None = None   # None = opacity fades
    portrait: LayoutBranch | None = Field(None, exclude_if=lambda v: v is None)
    landscape: LayoutBranch | None = Field(None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _units(self):
        u = self.grid.unit
        for name, v in (("labels.offset", self.labels.offset), ("labels.edge_clear", self.labels.edge_clear),
                        ("labels.text_clear", self.labels.text_clear), ("labels.card_gap", self.labels.card_gap),
                        ("zones.z2_pad", self.zones.z2_pad), ("zones.group_gap", self.zones.group_gap)):
            if v % u:
                raise ValueError(f"layout {name} = {v} is not a multiple of grid.unit {u}")
        return self


