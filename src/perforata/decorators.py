"""Decorators: instance cutout geometry onto PointCloud centers.

This is where the separation of concerns pays off: a decorator receives
grid centers (with ``angle``, ``size``, ``scale``, ``tag`` attributes) and
maps each *tag* to a shape recipe. Different tags — e.g. alternating rows
tagged "even"/"odd" — get different cutouts (e.g. up-triangles and
down-triangles), which is what enables triangle tessellations and
major/minor grid styling.

Output is a list of shape dicts:

    {"type": "circle",  "center": (x, y), "radius": r, "size": inscribed_dia}
    {"type": "polygon", "points": [(x, y), ...],       "size": inscribed_dia}

``size`` is always the inscribed diameter (the narrowest opening), the
measurement that matters for airflow and minimum-feature checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .graph import Node
from .pointcloud import PointCloud


@dataclass
class ShapeSpec:
    """A cutout recipe attached to a tag.

    shape    : "circle", "triangle", "square", "pentagon", "hexagon",
               or an integer >= 3 for an arbitrary regular n-gon.
    fill     : inscribed diameter (narrowest opening) relative to the
               local grid pitch: inscribed = size * fill * scale. At
               fill = 1.0 the opening equals the pitch, so neighboring
               cutouts touch; keep < 1.0 to leave walls between them.
    rotation : extra rotation in degrees on top of the point's angle.
    absolute : if True, ignore the point's local angle (global orientation).
    """

    shape: str | int = "hexagon"
    fill: float = 0.7
    rotation: float = 0.0
    absolute: bool = False

    @property
    def n_sides(self) -> int | None:
        if self.shape == "circle":
            return None
        if isinstance(self.shape, int):
            return self.shape
        return {"triangle": 3, "square": 4, "pentagon": 5,
                "hexagon": 6}[self.shape]


class ShapeInstancer(Node):
    """Instance shapes at every point, selecting the recipe by tag.

    rules : dict mapping tag -> ShapeSpec (or kwargs dict for one).
        The special key "*" is a fallback for unmatched tags. Points
        whose tag matches no rule (and no fallback) produce no shape.

    Example — triangles alternating orientation row by row::

        ShapeInstancer(rules={
            "even": ShapeSpec("triangle", fill=0.8, rotation=90),
            "odd":  ShapeSpec("triangle", fill=0.8, rotation=-90),
        })
    """

    def __init__(self, rules=None, **default_spec):
        super().__init__(rules=rules)
        if rules is None:
            rules = {"*": ShapeSpec(**default_spec) if default_spec
                     else ShapeSpec()}
        self.rules = {
            tag: (spec if isinstance(spec, ShapeSpec) else ShapeSpec(**spec))
            for tag, spec in rules.items()
        }

    def run(self, cloud: PointCloud) -> list[dict]:
        if len(cloud) == 0:
            return []
        tags = cloud.get("tag", "default")
        angles = cloud.get("angle", 0.0).astype(float)
        sizes = cloud.get("size", 1.0).astype(float)
        scales = cloud.get("scale", 1.0).astype(float)

        shapes: list[dict] = []
        for k in range(len(cloud)):
            spec = self.rules.get(tags[k]) or self.rules.get("*")
            if spec is None:
                continue
            # fill scales the inscribed diameter (narrowest opening), so
            # fill = 1.0 makes neighbors touch regardless of shape.
            # Convert to circumradius: apothem = r * cos(pi/n).
            r = 0.5 * sizes[k] * spec.fill * scales[k]
            n = spec.n_sides
            if n is not None:
                r /= math.cos(math.pi / n)
            if r <= 0:
                continue
            ori = math.radians(spec.rotation)
            if not spec.absolute:
                ori += angles[k]
            shapes.append(make_shape(spec.shape, cloud.x[k], cloud.y[k],
                                     r, ori))
        return shapes


def make_shape(shape, cx: float, cy: float, r: float, ori: float) -> dict:
    """Build one shape dict. ``r`` is the circumradius; the recorded
    ``size`` is the inscribed diameter."""
    if shape == "circle":
        return {"type": "circle", "center": (cx, cy),
                "radius": r, "size": 2.0 * r}

    n = shape if isinstance(shape, int) else \
        {"triangle": 3, "square": 4, "pentagon": 5, "hexagon": 6}[shape]
    # Squares get a flat-side-down default like the MVP; other n-gons
    # start with a vertex on the orientation axis.
    offset = math.pi / n if n == 4 else 0.0
    pts = []
    for k in range(n):
        a = ori + offset + 2 * math.pi * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    inscribed = 2.0 * r * math.cos(math.pi / n)
    return {"type": "polygon", "points": pts, "size": inscribed}


# ----------------------------------------------------------------------
# Shape-level helpers shared by ops / exporters
# ----------------------------------------------------------------------

def shape_bounds(shape: dict) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of one shape dict."""
    if shape["type"] == "circle":
        cx, cy = shape["center"]
        r = shape["radius"]
        return cx - r, cy - r, cx + r, cy + r
    xs = [p[0] for p in shape["points"]]
    ys = [p[1] for p in shape["points"]]
    return min(xs), min(ys), max(xs), max(ys)


def shapes_bounds(shapes: list[dict]) -> tuple[float, float, float, float]:
    """Overall bounds of a shape list."""
    bs = np.array([shape_bounds(s) for s in shapes])
    return bs[:, 0].min(), bs[:, 1].min(), bs[:, 2].max(), bs[:, 3].max()


def scale_shape(shape: dict, s: float) -> dict:
    """Uniformly scale a shape about the origin (returns a new dict)."""
    if shape["type"] == "circle":
        cx, cy = shape["center"]
        return {"type": "circle", "center": (cx * s, cy * s),
                "radius": shape["radius"] * s, "size": shape["size"] * s}
    return {"type": "polygon",
            "points": [(x * s, y * s) for x, y in shape["points"]],
            "size": shape["size"] * s}
