"""Params-dict API: the shared contract between the CLI, the web
platform (perforataio, via Pyodide), and any future server.

The entry points take a plain JSON-safe **params dict** describing a
pipeline and run it through the engine::

    {
      "v": 1,
      "generator":  {"type": "HexGrid", "params": {"pitch": 9.0, ...}},
      "modifiers":  [{"type": "FieldModulate", "params": {...}}, ...],
      "rules":      {"*": {"shape": "hexagon", "fill": 0.8}},
      "manufacturing": {
          "min_hole": 0, "min_wall": 1.5, "target_d": 0,
          "crop": {"boundary": {"kind": "rect", "width": 250, "height": 180},
                   "mode": "cull", "margin": 0, "outline": true}
      }
    }

Core functions (dict in, dict out):

* :func:`evaluate` — run the pipeline, return shapes + stats + timings.
* :func:`export` — run the pipeline, return SVG/DXF bytes.
* :func:`validate` — schema-check a params dict, return a list of
  problems (empty = valid).

JSON-string wrappers (:func:`evaluate_json`, :func:`export_json`) are
provided for the Pyodide worker boundary.
"""

from __future__ import annotations

import json
import time

from .generators import CartesianGrid, HexGrid, ConcentricRings
from .fields import (ShapeGradient, LinearGradient, RadialGradient,
                     Expression)
from .modifiers import (Affine, FieldModulate, DensityWarp,
                        AttributeFilter, PolarWarp, TagFilter)
from .decorators import ShapeInstancer, ShapeSpec, shapes_bounds
from .ops import (Boundary, Crop, resolve_constraints, cutouts_only,
                  min_spacing)

#: Params-schema version this build understands.
SCHEMA_VERSION = 1

GENERATORS = {
    "CartesianGrid": CartesianGrid,
    "HexGrid": HexGrid,
    "ConcentricRings": ConcentricRings,
}

FIELDS = {"ShapeGradient", "LinearGradient", "RadialGradient", "Expression"}

MODIFIERS = {"Rotate", "Scale", "Shear", "Translate", "PolarWarp",
             "FieldModulate", "DensityWarp", "AttributeFilter", "TagFilter"}


def _build_field(fdef: dict):
    t = fdef["type"]
    p = dict(fdef.get("params", {}))
    fscale = p.pop("fscale", 1.0)
    if t == "ShapeGradient":
        field = ShapeGradient(**p)
    elif t == "LinearGradient":
        field = LinearGradient(**p)
    elif t == "RadialGradient":
        field = RadialGradient(**p)
    elif t == "Expression":
        field = Expression(p["expr"])
    else:
        raise ValueError(f"unknown field type {t!r}")
    if abs(fscale - 1.0) > 1e-9:
        field = field.scaled(fscale)
    return field


def _build_modifier(mdef: dict):
    t = mdef["type"]
    p = dict(mdef.get("params", {}))
    if t == "Rotate":
        return Affine.rotation(p.get("degrees", 0.0))
    if t == "Scale":
        return Affine.scaling(p.get("sx", 1.0), p.get("sy", 1.0))
    if t == "Shear":
        return Affine.shear(p.get("shx", 0.0), p.get("shy", 0.0))
    if t == "Translate":
        return Affine.translation(p.get("dx", 0.0), p.get("dy", 0.0))
    if t == "PolarWarp":
        return PolarWarp(r_offset=p.get("r_offset", 100.0))
    if t == "FieldModulate":
        field = _build_field(p.pop("field"))
        attr = p.pop("attr", "scale")
        return FieldModulate(attr, field, **p)
    if t == "DensityWarp":
        field = _build_field(p.pop("field"))
        return DensityWarp(field, **p)
    if t == "AttributeFilter":
        attr = p.pop("attr", "scale")
        return AttributeFilter(attr, **p)
    if t == "TagFilter":
        return TagFilter(*p.get("tags", ["default"]))
    raise ValueError(f"unknown modifier type {t!r}")


def validate(params: dict) -> list[str]:
    """Schema-check a params dict. Returns a list of human-readable
    problems; an empty list means the params look valid."""
    problems: list[str] = []
    if not isinstance(params, dict):
        return ["params must be a JSON object"]
    v = params.get("v", SCHEMA_VERSION)
    if not isinstance(v, int) or v > SCHEMA_VERSION:
        problems.append(f"unsupported schema version {v!r} "
                        f"(this build understands <= {SCHEMA_VERSION})")
    gdef = params.get("generator")
    if not isinstance(gdef, dict):
        problems.append('missing required "generator" object')
    elif gdef.get("type") not in GENERATORS:
        problems.append(
            f'unknown generator type {gdef.get("type")!r} '
            f"(expected one of {sorted(GENERATORS)})")
    for i, mdef in enumerate(params.get("modifiers", [])):
        if not isinstance(mdef, dict) or mdef.get("type") not in MODIFIERS:
            t = mdef.get("type") if isinstance(mdef, dict) else mdef
            problems.append(f"modifiers[{i}]: unknown type {t!r} "
                            f"(expected one of {sorted(MODIFIERS)})")
            continue
        p = mdef.get("params", {})
        if mdef["type"] in ("FieldModulate", "DensityWarp"):
            fdef = p.get("field")
            if not isinstance(fdef, dict):
                problems.append(f'modifiers[{i}]: missing "field" object')
            elif fdef.get("type") not in FIELDS:
                problems.append(
                    f"modifiers[{i}].field: unknown type "
                    f"{fdef.get('type')!r} (expected one of "
                    f"{sorted(FIELDS)})")
    rules = params.get("rules", {})
    if not isinstance(rules, dict):
        problems.append('"rules" must be an object of tag -> spec')
    mfg = params.get("manufacturing", {})
    if not isinstance(mfg, dict):
        problems.append('"manufacturing" must be an object')
    elif isinstance(mfg.get("crop"), dict):
        kind = mfg["crop"].get("boundary", {}).get("kind", "rect")
        if kind not in ("rect", "circle"):
            problems.append(
                f"manufacturing.crop.boundary.kind: unknown kind {kind!r} "
                f"(expected 'rect' or 'circle')")
    return problems


def _run(params: dict):
    """Execute the pipeline; return (cloud, shapes, stage timings in s)."""
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    gdef = params["generator"]
    gen = GENERATORS[gdef["type"]](**gdef.get("params", {}))
    cloud = gen.run()
    timings["generate"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    for mdef in params.get("modifiers", []):
        cloud = _build_modifier(mdef).run(cloud)
    timings["modify"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    rules = {tag: ShapeSpec(**spec)
             for tag, spec in params.get("rules", {"*": {}}).items()}
    shapes = ShapeInstancer(rules=rules).run(cloud)
    timings["instance"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    mfg = params.get("manufacturing", {})
    shapes = resolve_constraints(shapes,
                                 diameter=mfg.get("target_d") or 0,
                                 min_wall=mfg.get("min_wall") or 0,
                                 min_hole=mfg.get("min_hole") or 0)
    crop_def = mfg.get("crop")
    if crop_def:
        b = crop_def.get("boundary", {})
        if b.get("kind") == "circle":
            boundary = Boundary.circle(b.get("diameter", 120.0))
        else:
            boundary = Boundary.rect(b.get("width", 200.0),
                                     b.get("height", 150.0))
        shapes = Crop(boundary,
                      mode=crop_def.get("mode", "cull"),
                      margin=crop_def.get("margin", 0.0),
                      include_outline=crop_def.get("outline", True)
                      ).run(shapes)
    timings["constrain"] = time.perf_counter() - t0

    return cloud, shapes, timings


def _shape_json(s: dict) -> dict:
    """Compact JSON-friendly form: t=c|p, o=outline flag."""
    if s["type"] == "circle":
        cx, cy = s["center"]
        return {"t": "c", "x": round(cx, 4), "y": round(cy, 4),
                "r": round(s["radius"], 4),
                "o": bool(s.get("outline", False))}
    return {"t": "p",
            "pts": [[round(x, 4), round(y, 4)] for x, y in s["points"]],
            "o": bool(s.get("outline", False))}


def evaluate(params: dict) -> dict:
    """Run the pipeline described by ``params``; return a JSON-safe dict
    with ``shapes``, ``stats``, and ``timings_ms``."""
    total0 = time.perf_counter()
    cloud, shapes, timings = _run(params)

    t0 = time.perf_counter()
    cuts = cutouts_only(shapes)
    stats: dict = {"points": len(cloud), "cutouts": len(cuts)}
    if cuts:
        sizes = [s["size"] for s in cuts]
        stats["smallest_hole"] = round(min(sizes), 4)
        stats["largest_hole"] = round(max(sizes), 4)
        x0, y0, x1, y1 = shapes_bounds(cuts)
        stats["extent"] = [round(x1 - x0, 3), round(y1 - y0, 3)]
        # min_spacing is O(n^2)-ish; skip for very dense patterns.
        if len(cuts) <= 3000:
            gap = min_spacing(cuts)
            if gap is not None:
                stats["min_spacing"] = round(gap, 4)
    timings["stats"] = time.perf_counter() - t0
    timings["total"] = time.perf_counter() - total0

    return {
        "shapes": [_shape_json(s) for s in shapes],
        "stats": stats,
        "timings_ms": {k: round(v * 1000, 2) for k, v in timings.items()},
    }


def export(params: dict, fmt: str = "svg") -> bytes:
    """Run the pipeline and export the result as ``"svg"`` or ``"dxf"``
    bytes."""
    _, shapes, _ = _run(params)
    if fmt == "dxf":
        from .exporters import DXFExporter
        return DXFExporter().run(shapes)
    if fmt == "svg":
        from .exporters import SVGExporter
        return SVGExporter().run(shapes)
    raise ValueError(f"unknown export format {fmt!r} "
                     f"(expected 'svg' or 'dxf')")


# -- JSON-string wrappers (Pyodide worker boundary) ---------------------

def evaluate_json(params_json: str) -> str:
    """:func:`evaluate` with a JSON string in and out."""
    return json.dumps(evaluate(json.loads(params_json)))


def export_json(params_json: str, fmt: str = "svg") -> bytes:
    """:func:`export` with a JSON string in."""
    return export(json.loads(params_json), fmt)
