"""Geometry post-processing ops for manufacturability.

These nodes act on the final shape list (after decoration):

* :class:`Crop` — true boolean intersection with a boundary via shapely,
  so shapes straddling the panel edge are sliced flush (no jagged
  half-dropped rows). ``mode="cull"`` alternatively drops any shape not
  fully inside — often preferable for perforation panels, since sliver
  cuts at the edge can be unmanufacturable.
* :class:`MinHoleFilter` — drop cutouts below a minimum inscribed
  diameter (respect the cutter's minimum feature size).
* :class:`FitToSize` — uniformly scale the finished pattern to an exact
  physical diameter or W x H envelope.
"""

from __future__ import annotations

import math

from .graph import Node
from .decorators import shape_bounds, shapes_bounds, scale_shape


# ----------------------------------------------------------------------
# Boundary / crop
# ----------------------------------------------------------------------

def _to_shapely(shape: dict):
    from shapely.geometry import Point, Polygon
    if shape["type"] == "circle":
        return Point(shape["center"]).buffer(shape["radius"], quad_segs=32)
    return Polygon(shape["points"])


def _from_shapely(geom, size_hint: float) -> list[dict]:
    """Convert a shapely (Multi)Polygon back to shape dicts."""
    from shapely.geometry import MultiPolygon, Polygon
    out = []
    geoms = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    for g in geoms:
        if not isinstance(g, Polygon) or g.is_empty or g.area <= 0:
            continue
        pts = list(g.exterior.coords)[:-1]  # drop closing duplicate
        # Inscribed diameter estimate: twice the max inradius
        r_in = g.exterior.distance(g.centroid)
        out.append({"type": "polygon", "points": pts,
                    "size": min(size_hint, 2.0 * r_in)})
    return out


class Boundary:
    """A crop boundary: rectangle, circle, or arbitrary polygon."""

    def __init__(self, kind: str, **kw):
        self.kind = kind
        self.kw = kw

    def __repr__(self):
        kv = ", ".join(f"{k}={v!r}" for k, v in self.kw.items())
        return f"Boundary.{self.kind}({kv})"

    @classmethod
    def rect(cls, width: float, height: float, center=(0.0, 0.0)):
        return cls("rect", width=width, height=height, center=center)

    @classmethod
    def circle(cls, diameter: float, center=(0.0, 0.0)):
        return cls("circle", diameter=diameter, center=center)

    @classmethod
    def polygon(cls, points):
        return cls("polygon", points=list(points))

    def to_shapely(self):
        from shapely.geometry import Point, Polygon, box
        if self.kind == "rect":
            cx, cy = self.kw["center"]
            hw, hh = self.kw["width"] / 2, self.kw["height"] / 2
            return box(cx - hw, cy - hh, cx + hw, cy + hh)
        if self.kind == "circle":
            return Point(self.kw["center"]).buffer(
                self.kw["diameter"] / 2, quad_segs=64)
        return Polygon(self.kw["points"])

    def outline_shape(self) -> dict:
        """The boundary itself as a shape dict (e.g. the panel outline
        to cut, exported on its own layer). Tagged ``outline: True`` so
        previews and cutout statistics can exclude it."""
        if self.kind == "circle":
            return {"type": "circle", "center": tuple(self.kw["center"]),
                    "radius": self.kw["diameter"] / 2,
                    "size": self.kw["diameter"], "outline": True}
        geom = self.to_shapely()
        pts = list(geom.exterior.coords)[:-1]
        return {"type": "polygon", "points": pts,
                "size": min(self.kw.get("width", 0) or 1e18,
                            self.kw.get("height", 0) or 1e18),
                "outline": True}


class Crop(Node):
    """Crop shapes to a boundary.

    mode:
        "slice"  — boolean-intersect straddling shapes with the boundary
                   (flush manufactured edge).
        "cull"   — keep only shapes entirely inside the boundary.
        "center" — keep shapes whose centroid is inside.

    margin : shrink the effective boundary inward by this much before
        cropping (keeps a solid rim of material near the panel edge).
    include_outline : if True, append the boundary outline as a final
        shape (the panel cut itself).
    """

    def __init__(self, boundary: Boundary, mode: str = "slice",
                 margin: float = 0.0, include_outline: bool = False):
        super().__init__(boundary=boundary, mode=mode, margin=margin,
                         include_outline=include_outline)
        self.boundary = boundary
        self.mode = mode
        self.margin = float(margin)
        self.include_outline = include_outline

    def run(self, shapes: list[dict]) -> list[dict]:
        bound = self.boundary.to_shapely()
        if self.margin > 0:
            bound_eff = bound.buffer(-self.margin)
        else:
            bound_eff = bound

        out: list[dict] = []
        for s in shapes:
            g = _to_shapely(s)
            if bound_eff.contains(g):
                out.append(s)
                continue
            if self.mode == "cull":
                continue
            if self.mode == "center":
                from shapely.geometry import Point
                if bound_eff.contains(Point(_centroid(s))):
                    out.append(s)
                continue
            # slice mode: boolean intersection
            clipped = g.intersection(bound_eff)
            if not clipped.is_empty:
                out.extend(_from_shapely(clipped, s["size"]))

        if self.include_outline:
            out.append(self.boundary.outline_shape())
        return out


def _centroid(shape: dict) -> tuple[float, float]:
    if shape["type"] == "circle":
        return shape["center"]
    pts = shape["points"]
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def cutouts_only(shapes: list[dict]) -> list[dict]:
    """Filter out non-cutout shapes (e.g. the panel outline)."""
    return [s for s in shapes if not s.get("outline")]


def min_spacing(shapes: list[dict]) -> float | None:
    """Narrowest wall between any two cutouts (bounding-circle gap:
    exact for circles, slightly conservative for polygons). The panel
    outline is ignored. None if fewer than two cutouts."""
    cuts = cutouts_only(shapes)
    if len(cuts) < 2:
        return None
    worst = MinWall._min_gap(cuts)
    return None if worst is None else worst[0]


def edge_clearance(shapes: list[dict], boundary: "Boundary") -> float | None:
    """Smallest distance from any cutout to the panel boundary edge.

    Uses exact shapely distances to the boundary's exterior ring. None
    if there are no cutouts. Negative values mean a cutout touches or
    crosses the edge (possible with crop modes "slice"/"center" or no
    cropping); the magnitude is how far it protrudes past the edge."""
    cuts = cutouts_only(shapes)
    if not cuts:
        return None
    from shapely.geometry import MultiPolygon, Point

    poly = boundary.to_shapely()
    edge = poly.exterior
    best = None
    for s in cuts:
        g = _to_shapely(s)
        if poly.contains(g):
            d = g.distance(edge)
        else:
            # Touching or crossing: negative clearance = how far the
            # shape protrudes past the edge (max distance of any vertex
            # of the outside piece back to the panel).
            outside = g.difference(poly)
            if outside.is_empty:
                d = 0.0
            else:
                parts = (outside.geoms
                         if isinstance(outside, MultiPolygon) else [outside])
                d = -max(Point(c).distance(poly)
                         for part in parts
                         for c in part.exterior.coords)
        if best is None or d < best:
            best = d
    return best


# ----------------------------------------------------------------------
# Manufacturability filters / sizing
# ----------------------------------------------------------------------

class MinHoleFilter(Node):
    """Drop cutouts whose inscribed diameter is below ``min_size``."""

    def __init__(self, min_size: float):
        super().__init__(min_size=min_size)
        self.min_size = float(min_size)

    def run(self, shapes: list[dict]) -> list[dict]:
        return [s for s in shapes if s["size"] >= self.min_size - 1e-9]


class FitToSize(Node):
    """Uniformly scale the whole pattern (about the origin) so its
    extent matches an exact physical size.

    Give either ``diameter`` (max extent) or ``width``/``height``
    (the pattern is scaled to fit inside, preserving aspect).
    """

    def __init__(self, diameter: float | None = None,
                 width: float | None = None, height: float | None = None):
        super().__init__(diameter=diameter, width=width, height=height)
        self.diameter, self.width, self.height = diameter, width, height

    def run(self, shapes: list[dict]) -> list[dict]:
        if not shapes:
            return shapes
        x0, y0, x1, y1 = shapes_bounds(shapes)
        w, h = x1 - x0, y1 - y0
        s = 1.0
        if self.diameter is not None and max(w, h) > 0:
            s = self.diameter / max(w, h)
        elif self.width or self.height:
            factors = []
            if self.width and w > 0:
                factors.append(self.width / w)
            if self.height and h > 0:
                factors.append(self.height / h)
            if factors:
                s = min(factors)
        if abs(s - 1.0) < 1e-12:
            return shapes
        return [scale_shape(sh, s) for sh in shapes]


def resolve_constraints(shapes: list[dict],
                        diameter: float = 0.0,
                        min_wall: float = 0.0,
                        min_hole: float = 0.0,
                        max_iter: int = 12) -> list[dict]:
    """Resolve size / wall / hole constraints simultaneously.

    A single sequential pass is destructive: MinWall shrinks every hole
    to satisfy the tightest (usually innermost) pair, MinHoleFilter then
    drops most of them, and nothing rescales back up. Like the MVP, we
    iterate::

        fit to target size -> shrink for min wall -> drop small holes

    Rescaling to the target size grows center distances as well as hole
    radii, which relaxes the wall constraint on the next pass — the loop
    converges to the largest holes that satisfy every constraint at the
    requested physical size.
    """
    for _ in range(max_iter):
        if not shapes:
            return shapes
        if diameter and diameter > 0:
            shapes = FitToSize(diameter=diameter).run(shapes)
        if min_wall > 0:
            shapes = MinWall(min_wall).run(shapes)
        n_before = len(shapes)
        if min_hole > 0:
            shapes = MinHoleFilter(min_hole).run(shapes)

        # Converged when nothing was dropped and (if requested) the
        # extent still matches the target size after wall shrinking.
        stable = len(shapes) == n_before
        if stable and diameter and diameter > 0 and shapes:
            x0, y0, x1, y1 = shapes_bounds(shapes)
            stable = abs(max(x1 - x0, y1 - y0) - diameter) < 1e-6 * diameter
        if stable and (not min_wall or not shapes or
                       (g := MinWall._min_gap(shapes)) is None or
                       g[0] >= min_wall - 1e-9):
            break
    return shapes


class MinWall(Node):
    """Report (and optionally enforce) minimum wall thickness between
    neighboring cutouts using bounding-circle gaps.

    With ``shrink=True``, all polygon/circle cutouts are shrunk about
    their own centroids by a uniform factor until the constraint holds
    (fixed-position centers keep the layout intact).
    """

    def __init__(self, min_wall: float, shrink: bool = True,
                 max_iter: int = 20):
        super().__init__(min_wall=min_wall, shrink=shrink)
        self.min_wall = float(min_wall)
        self.shrink = shrink
        self.max_iter = max_iter

    def run(self, shapes: list[dict]) -> list[dict]:
        if len(shapes) < 2 or self.min_wall <= 0:
            return shapes
        for _ in range(self.max_iter):
            worst = self._min_gap(shapes)
            if worst is None or worst[0] >= self.min_wall - 1e-9:
                return shapes
            if not self.shrink:
                print(f"Warning: narrowest wall {worst[0]:.3f} < "
                      f"min wall {self.min_wall}")
                return shapes
            gap, i, j = worst
            ci, cj = _centroid(shapes[i]), _centroid(shapes[j])
            d = math.hypot(cj[0] - ci[0], cj[1] - ci[1])
            r_sum = _outer_radius(shapes[i]) + _outer_radius(shapes[j])
            if r_sum <= 0:
                return shapes
            factor = min((d - self.min_wall) / r_sum, 0.98)
            if factor <= 0:
                print(f"Warning: min wall {self.min_wall} unsatisfiable — "
                      f"centers only {d:.3f} apart.")
                return shapes
            shapes = [_shrink_about_centroid(s, factor) for s in shapes]
        return shapes

    @staticmethod
    def _min_gap(shapes):
        centers = [_centroid(s) for s in shapes]
        radii = [_outer_radius(s) for s in shapes]
        cell = max(4.0 * max(radii), 1e-9)
        grid: dict[tuple[int, int], list[int]] = {}
        for idx, (x, y) in enumerate(centers):
            grid.setdefault((int(x // cell), int(y // cell)), []).append(idx)
        best = None
        for (gx, gy), idxs in grid.items():
            neigh = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neigh.extend(grid.get((gx + dx, gy + dy), []))
            for i in idxs:
                xi, yi = centers[i]
                for j in neigh:
                    if j <= i:
                        continue
                    d = math.hypot(centers[j][0] - xi, centers[j][1] - yi)
                    gap = d - radii[i] - radii[j]
                    if best is None or gap < best[0]:
                        best = (gap, i, j)
        return best


def _outer_radius(shape: dict) -> float:
    if shape["type"] == "circle":
        return shape["radius"]
    cx, cy = _centroid(shape)
    return max(math.hypot(px - cx, py - cy) for px, py in shape["points"])


def _shrink_about_centroid(shape: dict, factor: float) -> dict:
    cx, cy = _centroid(shape)
    if shape["type"] == "circle":
        return {"type": "circle", "center": (cx, cy),
                "radius": shape["radius"] * factor,
                "size": shape["size"] * factor}
    pts = [(cx + (px - cx) * factor, cy + (py - cy) * factor)
           for px, py in shape["points"]]
    return {"type": "polygon", "points": pts, "size": shape["size"] * factor}
