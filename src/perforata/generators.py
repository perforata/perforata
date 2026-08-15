"""Generators: nodes that produce a PointCloud of grid centers.

The theoretical framing is a 2D Bravais lattice: points are integer
combinations of two basis vectors, optionally decorated with a multi-point
unit cell (motif). Each emitted point carries attributes:

    angle : local orientation in radians (outward normal on radial grids)
    size  : local characteristic length (grid pitch / ring gap) — cutout
            sizes downstream are typically ``size * fill``
    tag   : label for decorator rules ("major"/"minor", "even"/"odd", ...)
    row/col or ring/index : lattice coordinates for downstream rules

Generators only place *centers*. What gets cut at each center is decided
entirely by decorators (see :mod:`perforata.decorators`), which is the key
separation from the old MVP.
"""

from __future__ import annotations

import math

import numpy as np

from .graph import Node
from .pointcloud import PointCloud


# ----------------------------------------------------------------------
# Cartesian lattices
# ----------------------------------------------------------------------

class Lattice(Node):
    """General 2D Bravais lattice generator.

    Points are ``origin + i * v1 + j * v2`` for integer ``(i, j)`` covering
    ``width x height`` (centered on the origin), plus an optional unit-cell
    ``motif`` of fractional offsets with tags.

    Parameters
    ----------
    v1, v2 : (dx, dy) basis vectors, in physical units.
    width, height : extent of the generated patch; points outside are culled.
    motif : list of (fu, fv, tag) — fractional positions inside the unit
        cell and the tag assigned to each. Default is a single point at
        the cell origin tagged "default".
    tag_rule : optional callable ``(i, j, base_tag) -> tag`` for row/column
        based tagging (e.g. alternate rows "up"/"down" for triangles).
    origin : (x, y) offset applied to every point — lets subclasses slide
        the lattice (e.g. by half a pitch) to balance edge margins.
    """

    def __init__(self, v1=(10.0, 0.0), v2=(0.0, 10.0),
                 width=100.0, height=100.0,
                 motif=None, tag_rule=None, origin=(0.0, 0.0)):
        super().__init__(v1=v1, v2=v2, width=width, height=height,
                         origin=origin)
        self.v1 = np.asarray(v1, dtype=float)
        self.v2 = np.asarray(v2, dtype=float)
        self.width = float(width)
        self.height = float(height)
        self.motif = motif or [(0.0, 0.0, "default")]
        self.tag_rule = tag_rule
        self.origin = np.asarray(origin, dtype=float)

    def run(self):
        B = np.column_stack([self.v1, self.v2])  # basis matrix
        if abs(np.linalg.det(B)) < 1e-12:
            raise ValueError("lattice basis vectors are collinear")

        # Find integer ranges that cover the target rectangle: transform the
        # rectangle corners into lattice coordinates and take the hull.
        hw, hh = self.width / 2.0, self.height / 2.0
        corners = np.array([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]])
        ij = (corners - self.origin) @ np.linalg.inv(B).T
        i_min, j_min = np.floor(ij.min(axis=0)).astype(int) - 1
        i_max, j_max = np.ceil(ij.max(axis=0)).astype(int) + 1

        # Local characteristic size: the smaller lattice pitch
        pitch = min(np.linalg.norm(self.v1), np.linalg.norm(self.v2))

        ii, jj = np.meshgrid(np.arange(i_min, i_max + 1),
                             np.arange(j_min, j_max + 1), indexing="ij")
        ii = ii.ravel()
        jj = jj.ravel()

        xs, ys, rows, cols, tags = [], [], [], [], []
        for fu, fv, base_tag in self.motif:
            pts = (np.stack([ii + fu, jj + fv], axis=1) @ B.T) + self.origin
            xs.append(pts[:, 0])
            ys.append(pts[:, 1])
            rows.append(jj)
            cols.append(ii)
            if self.tag_rule is not None:
                tags.append(np.array(
                    [self.tag_rule(int(i), int(j), base_tag)
                     for i, j in zip(ii, jj)], dtype=object))
            else:
                tags.append(np.full(ii.shape[0], base_tag, dtype=object))

        x = np.concatenate(xs)
        y = np.concatenate(ys)
        cloud = PointCloud(
            np.stack([x, y], axis=1),
            {
                "angle": np.zeros(x.shape[0]),
                "size": np.full(x.shape[0], pitch),
                "tag": np.concatenate(tags),
                "row": np.concatenate(rows),
                "col": np.concatenate(cols),
            },
        )
        # Cull to the requested rectangle
        keep = ((np.abs(cloud.x) <= hw + 1e-9) &
                (np.abs(cloud.y) <= hh + 1e-9))
        return cloud.filter(keep)


class CartesianGrid(Lattice):
    """Square/rectangular grid with optional row/column major-minor tagging.

    ``major_every`` tags every Nth row as "major" (others "minor"), the
    classic setup for alternating cutout styles per row. With
    ``alternate=True`` rows are tagged "even"/"odd" instead — handy for
    flipping triangles row by row.

    ``stagger`` shifts each row horizontally by a fraction of ``pitch_x``
    (brickwork); ``col_stagger`` shifts each column vertically by a
    fraction of ``pitch_y`` (vertical brickwork). Both may be combined.

    ``fit=True`` balances edge margins: each pitch is snapped so an
    integer number of cells spans the panel exactly (``span / round(span
    / pitch)``) and the lattice is shifted half a pitch on axes with an
    even cell count. The outermost points then sit exactly half a local
    pitch from every edge, so top/bottom and left/right margins match
    instead of leaving whatever remainder the raw pitch produced. The
    snapped pitches are stored back on ``pitch_x``/``pitch_y``.
    """

    def __init__(self, pitch_x=10.0, pitch_y=10.0, width=100.0, height=100.0,
                 stagger=0.0, col_stagger=0.0, major_every=0, alternate=False,
                 fit=False):
        self.pitch_x = float(pitch_x)
        self.pitch_y = float(pitch_y)
        self.fit = bool(fit)
        self.stagger = float(stagger)
        self.col_stagger = float(col_stagger)
        self.major_every = int(major_every)
        self.alternate = bool(alternate)

        origin = (0.0, 0.0)
        if self.fit:
            # A half stagger interleaves rows/columns onto a half-pitch
            # grid along that axis; fit that finer grid so staggered rows
            # don't land flush on the panel edge.
            sub_x = 2 if abs(self.stagger - 0.5) < 1e-9 else 1
            sub_y = 2 if abs(self.col_stagger - 0.5) < 1e-9 else 1
            self.pitch_x, ox = _fit_axis(self.pitch_x, float(width), sub_x)
            self.pitch_y, oy = _fit_axis(self.pitch_y, float(height), sub_y)
            origin = (ox, oy)

        if abs(self.stagger * self.col_stagger - 1.0) < 1e-9:
            raise ValueError(
                "stagger * col_stagger == 1 collapses the lattice "
                "(basis vectors become collinear)")

        def tag_rule(i, j, base):
            if self.major_every > 0 and j % self.major_every == 0:
                return "major"
            if self.alternate:
                return "even" if j % 2 == 0 else "odd"
            return "minor" if self.major_every > 0 else base

        # Staggers are expressed as sheared basis vectors: row stagger
        # (brickwork) shifts each row by stagger * pitch_x; column stagger
        # shifts each column by col_stagger * pitch_y.
        super().__init__(
            v1=(self.pitch_x, self.col_stagger * self.pitch_y),
            v2=(self.stagger * self.pitch_x, self.pitch_y),
            width=width, height=height,
            tag_rule=tag_rule,
            origin=origin,
        )
        self.params.update(pitch_x=self.pitch_x, pitch_y=self.pitch_y,
                           stagger=stagger, col_stagger=col_stagger,
                           major_every=major_every, alternate=alternate,
                           fit=fit)


def _fit_axis(pitch: float, span: float,
              subdiv: int = 1) -> tuple[float, float]:
    """Snap ``pitch`` so an integer number of cells covers ``span``
    exactly, and return ``(snapped_pitch, origin_offset)``.

    ``subdiv`` fits the grid at ``pitch / subdiv`` resolution — used when
    a half stagger interleaves rows onto a finer union grid along this
    axis. Points of the (union) grid sit at ``offset + k * q`` with
    ``q = snapped_pitch / subdiv``; the outermost land ``q / 2`` from
    both edges, so margins are balanced. Even cell counts need the
    half-cell offset because a centered lattice would otherwise put a
    point at 0 and leave a full cell of margin on one side.
    """
    if pitch <= 0 or span <= 0:
        return pitch, 0.0
    q = pitch / subdiv
    n = max(1, round(span / q))
    q = span / n
    return q * subdiv, (q / 2.0 if n % 2 == 0 else 0.0)


class HexGrid(CartesianGrid):
    """Triangular/hexagonal lattice (each point has 6 equidistant
    neighbors) — the natural layout for honeycomb perforations.

    With ``fit=True`` the horizontal and row pitches are snapped
    independently to balance edge margins, so the lattice may deviate
    slightly from perfectly equilateral spacing.
    """

    def __init__(self, pitch=10.0, width=100.0, height=100.0,
                 major_every=0, alternate=False, fit=False):
        super().__init__(
            pitch_x=pitch,
            pitch_y=pitch * math.sqrt(3) / 2.0,
            width=width, height=height,
            stagger=0.5,
            major_every=major_every, alternate=alternate,
            fit=fit,
        )
        self.params.update(pitch=pitch)


# ----------------------------------------------------------------------
# Radial / concentric generators (the old MVP's coordinate system)
# ----------------------------------------------------------------------

def ring_radius(n: int, r0: float, factor: float, mode: str) -> float:
    """Radius of the n-th ring for a given spacing law."""
    if mode == "exponential":
        if r0 <= 0:
            raise ValueError("exponential mode requires r0 > 0")
        return r0 * (factor ** n)
    if mode == "quadratic":
        return r0 + factor * (n ** 2)
    if mode == "linear":
        return r0 + factor * n
    raise ValueError(f"unknown mode {mode!r}")


class ConcentricRings(Node):
    """Concentric-ring point generator (replicates the MVP's layout).

    Rings sit at radii given by the spacing ``mode`` (exponential /
    quadratic / linear). Cutout centers are distributed along each ring;
    the point count per ring follows the local gap so packing density
    stays uniform, unless ``spokes`` forces a fixed count.

    Emitted attributes:
        angle : outward normal at each point (radial direction)
        size  : the local ring gap (dR) — decorator sizes scale off this
        ring  : ring index (0 = innermost)
        index : position index within the ring
        tag   : "major" every ``major_every`` rings if set, else
                "even"/"odd" if ``alternate``, else "default"

    twist : degrees of cumulative rotation per ring (spiral arms).
    stagger : fraction of each ring's own pitch to offset per ring
        (0.5 = brickwork), accumulated ring by ring.
    invert : place the smallest/densest rings on the outside.
    """

    def __init__(self, rings=15, r0=10.0, factor=1.15, mode="exponential",
                 twist=0.0, spokes=0, stagger=0.0, invert=False,
                 phase=0.0, major_every=0, alternate=False):
        super().__init__(rings=rings, r0=r0, factor=factor, mode=mode,
                         twist=twist, spokes=spokes, stagger=stagger,
                         invert=invert, phase=phase,
                         major_every=major_every, alternate=alternate)

    def run(self):
        p = self.params
        rings, r0, factor, mode = p["rings"], p["r0"], p["factor"], p["mode"]
        invert = p["invert"]

        xs, ys, angles, sizes, ring_idx, item_idx, tags = \
            [], [], [], [], [], [], []
        cumulative_stagger = 0.0

        for i in range(rings):
            eff = (rings - 1 - i) if invert else i
            R = ring_radius(eff, r0, factor, mode)
            if eff == 0:
                dR = ring_radius(1, r0, factor, mode) - R
            else:
                dR = R - ring_radius(eff - 1, r0, factor, mode)
            if dR <= 0:
                continue

            if invert:
                max_R = ring_radius(rings - 1, r0, factor, mode)
                min_R = ring_radius(0, r0, factor, mode)
                phys_R = max_R - R + min_R
            else:
                phys_R = R

            if p["spokes"] > 0:
                n_items = p["spokes"]
            else:
                n_items = max(1, int(round(2 * math.pi * phys_R / dR)))

            phase = (p["phase"] / 360.0
                     + p["twist"] / 360.0 * i
                     + cumulative_stagger) % 1.0
            cumulative_stagger += p["stagger"] / n_items

            if p["major_every"] > 0:
                tag = "major" if i % p["major_every"] == 0 else "minor"
            elif p["alternate"]:
                tag = "even" if i % 2 == 0 else "odd"
            else:
                tag = "default"

            j = np.arange(n_items)
            theta = (j / n_items + phase) * 2 * math.pi
            xs.append(phys_R * np.cos(theta))
            ys.append(phys_R * np.sin(theta))
            angles.append(theta)
            sizes.append(np.full(n_items, dR))
            ring_idx.append(np.full(n_items, i))
            item_idx.append(j)
            tags.append(np.full(n_items, tag, dtype=object))

        if not xs:
            return PointCloud.empty()

        x = np.concatenate(xs)
        y = np.concatenate(ys)
        return PointCloud(
            np.stack([x, y], axis=1),
            {
                "angle": np.concatenate(angles),
                "size": np.concatenate(sizes),
                "ring": np.concatenate(ring_idx),
                "index": np.concatenate(item_idx),
                "tag": np.concatenate(tags),
            },
        )
