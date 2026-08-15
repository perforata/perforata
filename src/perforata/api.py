"""Params-dict API: the shared contract between the CLI, the web
platform (perforataio, via Pyodide), and any future server.

The entry points take a plain JSON-safe **params dict** describing a
pipeline and run it through the engine::

    {
      "version": 2,
      "generator":  {"type": "HexGrid", "pitch": 9.0, ...},
      "modifiers":  [{"type": "FieldModulate",
                      "field": {"type": "RadialGradient"}, ...}],
      "rules":      {"*": {"shape": "hexagon", "fill": 0.8}},
      "manufacturing": {
          "min_hole": 0, "min_wall": 1.5, "target_d": 0,
          "crop": {"boundary": {"kind": "rect", "width": 250,
                                "height": 180},
                   "mode": "cull", "margin": 0, "outline": true}
      }
    }

The schema is defined by pydantic models in :mod:`perforata.schema`
(:class:`~perforata.schema.PipelineDef`); v1 documents (0.3.x, node
params nested under ``"params"``) are migrated automatically with a
:class:`DeprecationWarning`.

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
import warnings

from pydantic import ValidationError

from .schema import SCHEMA_VERSION, PipelineDef, detect_version, migrate
from .decorators import ShapeInstancer, shapes_bounds
from .ops import resolve_constraints, cutouts_only, min_spacing

__all__ = ["SCHEMA_VERSION", "evaluate", "export", "validate",
           "evaluate_json", "export_json"]


def _coerce(params: dict | PipelineDef) -> PipelineDef:
    """Validate a raw params dict into a PipelineDef, auto-migrating v1
    documents (with a deprecation warning). Raises ValidationError."""
    if isinstance(params, PipelineDef):
        return params
    if isinstance(params, dict) and detect_version(params) == 1:
        warnings.warn(
            "params schema v1 (nested 'params' keys) is deprecated; "
            "migrate documents to schema v2 (flat node keys) — see "
            "perforata.schema.migrate()",
            DeprecationWarning, stacklevel=3)
        params = migrate(params)
    return PipelineDef.model_validate(params)


def validate(params: dict) -> list[str]:
    """Schema-check a params dict. Returns a list of human-readable
    problems; an empty list means the params look valid."""
    if not isinstance(params, dict):
        return ["params must be a JSON object"]
    v = params.get("version", params.get("v"))
    if v is not None and (not isinstance(v, int) or v > SCHEMA_VERSION):
        return [f"unsupported schema version {v!r} "
                f"(this build understands <= {SCHEMA_VERSION})"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _coerce(params)
    except ValidationError as exc:
        return [_format_error(e) for e in exc.errors()]
    return []


def _format_error(err: dict) -> str:
    """One pydantic error dict -> a compact, path-addressed message."""
    loc = ".".join(str(p) for p in err["loc"]) or "<root>"
    return f"{loc}: {err['msg']}"


def _run(pipeline: PipelineDef):
    """Execute the pipeline; return (cloud, shapes, stage timings in s)."""
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    cloud = pipeline.generator.build().run()
    timings["generate"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    for mdef in pipeline.modifiers:
        cloud = mdef.build().run(cloud)
    timings["modify"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    rules = {tag: rule.build() for tag, rule in pipeline.rules.items()}
    shapes = ShapeInstancer(rules=rules).run(cloud)
    timings["instance"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    mfg = pipeline.manufacturing
    shapes = resolve_constraints(shapes,
                                 diameter=mfg.target_d,
                                 min_wall=mfg.min_wall,
                                 min_hole=mfg.min_hole)
    if mfg.crop is not None:
        shapes = mfg.crop.build().run(shapes)
    timings["constrain"] = time.perf_counter() - t0

    return cloud, shapes, timings


def _shape_json(s: dict) -> dict:
    """Compact JSON-friendly form: t=c|p, o=outline flag."""
    if s["type"] == "circle":
        cx, cy = s["center"]
        return {"t": "c", "x": round(float(cx), 4), "y": round(float(cy), 4),
                "r": round(float(s["radius"]), 4),
                "o": bool(s.get("outline", False))}
    return {"t": "p",
            "pts": [[round(float(x), 4), round(float(y), 4)]
                    for x, y in s["points"]],
            "o": bool(s.get("outline", False))}


def evaluate(params: dict | PipelineDef) -> dict:
    """Run the pipeline described by ``params``; return a JSON-safe dict
    with ``shapes``, ``stats``, and ``timings_ms``.

    Raises :class:`pydantic.ValidationError` on invalid params (use
    :func:`validate` first for a friendly problem list)."""
    pipeline = _coerce(params)
    total0 = time.perf_counter()
    cloud, shapes, timings = _run(pipeline)

    t0 = time.perf_counter()
    cuts = cutouts_only(shapes)
    stats: dict = {"points": len(cloud), "cutouts": len(cuts)}
    if cuts:
        sizes = [s["size"] for s in cuts]
        stats["smallest_hole"] = round(float(min(sizes)), 4)
        stats["largest_hole"] = round(float(max(sizes)), 4)
        x0, y0, x1, y1 = shapes_bounds(cuts)
        stats["extent"] = [round(float(x1 - x0), 3),
                           round(float(y1 - y0), 3)]
        # min_spacing is O(n^2)-ish; skip for very dense patterns.
        if len(cuts) <= 3000:
            gap = min_spacing(cuts)
            if gap is not None:
                stats["min_spacing"] = round(float(gap), 4)
    timings["stats"] = time.perf_counter() - t0
    timings["total"] = time.perf_counter() - total0

    return {
        "shapes": [_shape_json(s) for s in shapes],
        "stats": stats,
        "timings_ms": {k: round(v * 1000, 2) for k, v in timings.items()},
    }


def export(params: dict | PipelineDef, fmt: str = "svg") -> bytes:
    """Run the pipeline and export the result as ``"svg"`` or ``"dxf"``
    bytes."""
    pipeline = _coerce(params)
    _, shapes, _ = _run(pipeline)
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
