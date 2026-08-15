"""Typed pipeline-params schema (pydantic v2 models).

This module is the single source of truth for the params contract
shared by the CLI, the web platform (perforataio, via Pyodide), and any
future server. It contains **models only** — engine imports happen
lazily inside ``build()`` methods so the module stays importable for
pure validation tooling (e.g. JSON Schema export) without numpy.

Schema v2 layout (flat nodes — the ``type`` key is the discriminator)::

    {
      "version": 2,
      "name": "untitled",
      "generator":  {"type": "HexGrid", "pitch": 9.0, ...},
      "modifiers":  [{"type": "FieldModulate",
                      "field": {"type": "RadialGradient", ...}, ...}],
      "rules":      {"*": {"shape": "hexagon", "fill": 0.8}},
      "manufacturing": {
          "min_hole": 0, "min_wall": 1.5, "target_d": 0,
          "crop": {"boundary": {"kind": "rect",
                                "width": 250, "height": 180},
                   "mode": "cull", "margin": 0, "outline": true}
      }
    }

Schema v1 (0.3.x) nested node parameters under a ``"params"`` key;
:func:`migrate` hoists them mechanically.
"""

from __future__ import annotations

import base64
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Params-schema version this build understands.
SCHEMA_VERSION = 2


class StrictModel(BaseModel):
    """Base model: unknown keys are validation errors, not silent noise."""

    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------
# Fields (discriminated by "type")
# ----------------------------------------------------------------------

class _FieldDefBase(StrictModel):
    """Shared knobs for every field: ``fscale`` zooms the field about
    the center of the unit square (see ``Field.scaled``); ``offset_u``/
    ``offset_v`` translate it in uv space afterward (see
    ``Field.offset``)."""

    fscale: float = Field(1.0, gt=0)
    offset_u: float = 0.0
    offset_v: float = 0.0

    def _build_raw(self):
        raise NotImplementedError

    def build(self):
        """Return the engine Field, with ``fscale`` then ``offset``
        applied (in that order — offset shifts the already-scaled
        field)."""
        field = self._build_raw()
        if abs(self.fscale - 1.0) > 1e-9:
            field = field.scaled(self.fscale)
        if abs(self.offset_u) > 1e-9 or abs(self.offset_v) > 1e-9:
            field = field.offset(self.offset_u, self.offset_v)
        return field


class LinearGradientDef(_FieldDefBase):
    type: Literal["LinearGradient"]
    angle_deg: float = 0.0

    def _build_raw(self):
        from .fields import LinearGradient
        return LinearGradient(angle_deg=self.angle_deg)


class RadialGradientDef(_FieldDefBase):
    type: Literal["RadialGradient"]
    invert: bool = False

    def _build_raw(self):
        from .fields import RadialGradient
        return RadialGradient(invert=self.invert)


class ShapeGradientDef(_FieldDefBase):
    type: Literal["ShapeGradient"]
    shape: Literal["circle", "square", "rectangle", "hexagon"] = "circle"
    falloff: Literal["linear", "quadratic", "exponential",
                     "gaussian", "sphere"] = "linear"
    k: float = 3.0
    invert: bool = False
    width: float = Field(1.0, gt=0)
    height: float = Field(1.0, gt=0)

    def _build_raw(self):
        from .fields import ShapeGradient
        return ShapeGradient(shape=self.shape, falloff=self.falloff,
                             k=self.k, invert=self.invert,
                             width=self.width, height=self.height)


class ExpressionDef(_FieldDefBase):
    type: Literal["Expression"]
    expr: str

    def _build_raw(self):
        from .fields import Expression
        return Expression(self.expr)


class ImageFieldDef(_FieldDefBase):
    """A browser-uploaded (or otherwise client-side) image, shipped as a
    base64-encoded uint8 luminance grid so the params document stays
    JSON-safe for persistence (localStorage, URL hashes, Postgres
    JSONB). ``luminance_b64`` must decode to exactly ``w * h`` bytes,
    row-major, white=255/black=0 (see :func:`perforata.fields._to_luminance`).
    """

    type: Literal["ImageField"]
    luminance_b64: str
    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)
    invert: bool = False
    fit: Literal["stretch", "contain"] = "contain"
    bg: float = Field(0.0, ge=0.0, le=1.0)
    smooth: bool = True

    @model_validator(mode="after")
    def _check_payload(self):
        try:
            raw = base64.b64decode(self.luminance_b64, validate=True)
        except Exception as exc:
            raise ValueError(
                f"luminance_b64 is not valid base64: {exc}") from exc
        expected = self.w * self.h
        if len(raw) != expected:
            raise ValueError(
                f"luminance_b64 decodes to {len(raw)} bytes, expected "
                f"w*h = {expected} (w={self.w}, h={self.h})")
        return self

    def _build_raw(self):
        import numpy as np
        from .fields import ImageField
        raw = base64.b64decode(self.luminance_b64)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(self.h, self.w)
        return ImageField(arr, invert=self.invert, fit=self.fit,
                          bg=self.bg, smooth=self.smooth)


FieldDef = Annotated[
    Union[LinearGradientDef, RadialGradientDef, ShapeGradientDef,
          ExpressionDef, ImageFieldDef],
    Field(discriminator="type"),
]


# ----------------------------------------------------------------------
# Generators (discriminated by "type")
# ----------------------------------------------------------------------

class CartesianGridDef(StrictModel):
    type: Literal["CartesianGrid"]
    pitch_x: float = Field(10.0, gt=0)
    pitch_y: float = Field(10.0, gt=0)
    width: float = Field(100.0, gt=0)
    height: float = Field(100.0, gt=0)
    stagger: float = 0.0
    col_stagger: float = 0.0
    major_every: int = Field(0, ge=0)
    alternate: bool = False
    fit: bool = False

    def build(self):
        from .generators import CartesianGrid
        return CartesianGrid(**self.model_dump(exclude={"type"}))


class HexGridDef(StrictModel):
    type: Literal["HexGrid"]
    pitch: float = Field(10.0, gt=0)
    width: float = Field(100.0, gt=0)
    height: float = Field(100.0, gt=0)
    major_every: int = Field(0, ge=0)
    alternate: bool = False
    fit: bool = False

    def build(self):
        from .generators import HexGrid
        return HexGrid(**self.model_dump(exclude={"type"}))


class ConcentricRingsDef(StrictModel):
    type: Literal["ConcentricRings"]
    rings: int = Field(15, ge=1)
    r0: float = Field(10.0, gt=0)
    factor: float = 1.15
    mode: Literal["exponential", "quadratic", "linear"] = "exponential"
    twist: float = 0.0
    spokes: int = Field(0, ge=0)
    stagger: float = 0.0
    invert: bool = False
    phase: float = 0.0
    major_every: int = Field(0, ge=0)
    alternate: bool = False

    def build(self):
        from .generators import ConcentricRings
        return ConcentricRings(**self.model_dump(exclude={"type"}))


GeneratorDef = Annotated[
    Union[CartesianGridDef, HexGridDef, ConcentricRingsDef],
    Field(discriminator="type"),
]


# ----------------------------------------------------------------------
# Modifiers (discriminated by "type")
# ----------------------------------------------------------------------

#: World rectangle mapped to a field's unit square: None (cloud bbox),
#: "symmetric" (origin-centered box), or explicit (xmin, ymin, xmax, ymax).
RegionDef = Union[Literal["symmetric"],
                  tuple[float, float, float, float], None]


class RotateDef(StrictModel):
    type: Literal["Rotate"]
    degrees: float = 0.0

    def build(self):
        from .modifiers import Affine
        return Affine.rotation(self.degrees)


class ScaleDef(StrictModel):
    type: Literal["Scale"]
    sx: float = 1.0
    sy: float = 1.0

    def build(self):
        from .modifiers import Affine
        return Affine.scaling(self.sx, self.sy)


class ShearDef(StrictModel):
    type: Literal["Shear"]
    shx: float = 0.0
    shy: float = 0.0

    def build(self):
        from .modifiers import Affine
        return Affine.shear(self.shx, self.shy)


class TranslateDef(StrictModel):
    type: Literal["Translate"]
    dx: float = 0.0
    dy: float = 0.0

    def build(self):
        from .modifiers import Affine
        return Affine.translation(self.dx, self.dy)


class PolarWarpDef(StrictModel):
    type: Literal["PolarWarp"]
    r_offset: float = 100.0

    def build(self):
        from .modifiers import PolarWarp
        return PolarWarp(r_offset=self.r_offset)


class FieldModulateDef(StrictModel):
    type: Literal["FieldModulate"]
    field: FieldDef
    attr: str = "scale"
    lo: float = 0.0
    hi: float = 1.0
    mode: Literal["multiply", "set", "add"] = "multiply"
    curve: Literal["linear", "quadratic", "exponential",
                   "gaussian", "sphere"] = "linear"
    k: float = 3.0
    region: RegionDef = None

    def build(self):
        from .modifiers import FieldModulate
        return FieldModulate(self.attr, self.field.build(),
                             lo=self.lo, hi=self.hi, mode=self.mode,
                             curve=self.curve, k=self.k,
                             region=self.region)


class DensityWarpDef(StrictModel):
    type: Literal["DensityWarp"]
    field: FieldDef
    strength: float = Field(1.0, ge=0, le=1)
    axes: Literal["both", "x", "y"] = "both"
    resolution: int = Field(256, ge=8)
    region: RegionDef = None

    def build(self):
        from .modifiers import DensityWarp
        return DensityWarp(self.field.build(), strength=self.strength,
                           axes=self.axes, resolution=self.resolution,
                           region=self.region)


class AttributeFilterDef(StrictModel):
    type: Literal["AttributeFilter"]
    attr: str = "scale"
    lo: float | None = None   # None = unbounded below
    hi: float | None = None   # None = unbounded above

    def build(self):
        import math
        from .modifiers import AttributeFilter
        lo = -math.inf if self.lo is None else self.lo
        hi = math.inf if self.hi is None else self.hi
        return AttributeFilter(self.attr, lo=lo, hi=hi)


class TagFilterDef(StrictModel):
    type: Literal["TagFilter"]
    tags: list[str] = Field(default_factory=lambda: ["default"])

    def build(self):
        from .modifiers import TagFilter
        return TagFilter(*self.tags)


ModifierDef = Annotated[
    Union[RotateDef, ScaleDef, ShearDef, TranslateDef, PolarWarpDef,
          FieldModulateDef, DensityWarpDef, AttributeFilterDef,
          TagFilterDef],
    Field(discriminator="type"),
]


# ----------------------------------------------------------------------
# Rules / manufacturing
# ----------------------------------------------------------------------

class ShapeRule(StrictModel):
    """A cutout recipe attached to a tag (see decorators.ShapeSpec)."""

    shape: Union[Literal["circle", "triangle", "square", "pentagon",
                         "hexagon"],
                 Annotated[int, Field(ge=3)]] = "hexagon"
    fill: float = Field(0.7, gt=0, le=1)
    rotation: float = 0.0
    absolute: bool = False

    def build(self):
        from .decorators import ShapeSpec
        return ShapeSpec(shape=self.shape, fill=self.fill,
                         rotation=self.rotation, absolute=self.absolute)


class RectBoundaryDef(StrictModel):
    kind: Literal["rect"]
    width: float = Field(200.0, gt=0)
    height: float = Field(150.0, gt=0)


class CircleBoundaryDef(StrictModel):
    kind: Literal["circle"]
    diameter: float = Field(120.0, gt=0)


BoundaryDef = Annotated[Union[RectBoundaryDef, CircleBoundaryDef],
                        Field(discriminator="kind")]


class CropDef(StrictModel):
    boundary: BoundaryDef
    mode: Literal["slice", "cull", "center"] = "cull"
    margin: float = Field(0.0, ge=0)
    outline: bool = True

    def build(self):
        from .ops import Boundary, Crop
        b = self.boundary
        if b.kind == "circle":
            boundary = Boundary.circle(b.diameter)
        else:
            boundary = Boundary.rect(b.width, b.height)
        return Crop(boundary, mode=self.mode, margin=self.margin,
                    include_outline=self.outline)


class Manufacturing(StrictModel):
    min_hole: float = Field(0.0, ge=0)
    min_wall: float = Field(0.0, ge=0)
    target_d: float = Field(0.0, ge=0)
    crop: CropDef | None = None


# ----------------------------------------------------------------------
# The pipeline document
# ----------------------------------------------------------------------

class PipelineDef(StrictModel):
    """A complete pipeline params document (schema v2)."""

    version: Literal[2] = 2
    name: str = "untitled"
    generator: GeneratorDef
    modifiers: list[ModifierDef] = Field(default_factory=list)
    rules: dict[str, ShapeRule] = Field(
        default_factory=lambda: {"*": ShapeRule()})
    manufacturing: Manufacturing = Field(default_factory=Manufacturing)


# ----------------------------------------------------------------------
# Typed output (evaluate results)
# ----------------------------------------------------------------------

class CircleShape(StrictModel):
    """Compact JSON circle: t=c, o=outline flag."""

    t: Literal["c"]
    x: float
    y: float
    r: float
    o: bool = False


class PolygonShape(StrictModel):
    """Compact JSON polygon: t=p, o=outline flag."""

    t: Literal["p"]
    pts: list[tuple[float, float]]
    o: bool = False


ShapeJSON = Annotated[Union[CircleShape, PolygonShape],
                      Field(discriminator="t")]


class Stats(StrictModel):
    points: int
    cutouts: int
    smallest_hole: float | None = None
    largest_hole: float | None = None
    extent: tuple[float, float] | None = None
    min_spacing: float | None = None


class EvaluationResult(StrictModel):
    shapes: list[ShapeJSON]
    stats: Stats
    timings_ms: dict[str, float]


# ----------------------------------------------------------------------
# Version detection & migration (v1 -> v2)
# ----------------------------------------------------------------------

def detect_version(params: dict) -> int:
    """Best-effort schema version of a raw params dict.

    Order: explicit ``version`` (v2+) or ``v`` (v1) keys, else the
    nested-``params`` layout marks a v1 document; default is current.
    """
    v = params.get("version", params.get("v"))
    if v is not None:
        return v
    gdef = params.get("generator")
    if isinstance(gdef, dict) and "params" in gdef:
        return 1
    mods = params.get("modifiers")
    if isinstance(mods, list) and any(
            isinstance(m, dict) and "params" in m for m in mods):
        return 1
    return SCHEMA_VERSION


def _flatten_node(node):
    """Hoist a v1 node's nested ``params`` up one level (recursing into
    nested ``field`` definitions)."""
    if not isinstance(node, dict):
        return node
    out = {k: v for k, v in node.items() if k != "params"}
    p = node.get("params", {})
    if isinstance(p, dict):
        p = dict(p)
        if isinstance(p.get("field"), dict):
            p["field"] = _flatten_node(p["field"])
        out.update(p)
    return out


def migrate(params: dict) -> dict:
    """Migrate a params dict to the current schema version.

    v1 documents (``"v": 1`` / nested ``"params"``) are converted
    mechanically: node params are hoisted one level, ``v`` becomes
    ``version``, and a missing crop-boundary ``kind`` defaults to
    ``"rect"``. Already-current documents come back (shallow-copied)
    unchanged.
    """
    if not isinstance(params, dict):
        return params
    version = detect_version(params)
    if version != 1:
        out = dict(params)
        out.pop("v", None)
        out.setdefault("version", version)
        return out

    out = {k: v for k, v in params.items() if k != "v"}
    out["version"] = SCHEMA_VERSION
    if isinstance(out.get("generator"), dict):
        out["generator"] = _flatten_node(out["generator"])
    if isinstance(out.get("modifiers"), list):
        out["modifiers"] = [_flatten_node(m) for m in out["modifiers"]]
    mfg = out.get("manufacturing")
    if isinstance(mfg, dict) and isinstance(mfg.get("crop"), dict):
        mfg = dict(mfg)
        crop = dict(mfg["crop"])
        boundary = crop.get("boundary")
        if isinstance(boundary, dict):
            boundary = dict(boundary)
            boundary.setdefault("kind", "rect")   # v1 default
            crop["boundary"] = boundary
        mfg["crop"] = crop
        out["manufacturing"] = mfg
    return out
