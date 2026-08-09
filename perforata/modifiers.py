"""Modifiers: nodes that transform a PointCloud.

Two families:

* Well-posed linear algebra: :class:`Affine` (and its convenience
  constructors for rotate / scale / shear / translate) applies a 2x3
  augmented matrix to point positions and keeps orientation attributes
  consistent.
* Attribute modulation: :class:`FieldModulate` samples a
  :class:`~perforata.fields.Field` (e.g. an image) at each point and
  drives a point attribute with the result — this is the "convolve a
  logo with the grid" mechanism.
"""

from __future__ import annotations

import math

import numpy as np

from .graph import Node
from .pointcloud import PointCloud
from .fields import Field


# ----------------------------------------------------------------------
# Linear algebra
# ----------------------------------------------------------------------

class Affine(Node):
    """Apply an affine transform ``p' = A @ p + t`` to point positions.

    ``matrix`` is a 2x2 array-like, ``translate`` a length-2 offset.
    The per-point ``angle`` attribute is updated by the rotational part
    of A, and the ``size``/``scale`` attributes by its mean scale factor,
    so oriented, sized cutouts stay consistent under the transform.
    """

    def __init__(self, matrix=((1.0, 0.0), (0.0, 1.0)), translate=(0.0, 0.0)):
        super().__init__(matrix=matrix, translate=translate)
        self.A = np.asarray(matrix, dtype=float).reshape(2, 2)
        self.t = np.asarray(translate, dtype=float).reshape(2)

    # -- convenience constructors --------------------------------------
    @classmethod
    def rotation(cls, degrees: float) -> "Affine":
        a = math.radians(degrees)
        c, s = math.cos(a), math.sin(a)
        return cls(matrix=((c, -s), (s, c)))

    @classmethod
    def scaling(cls, sx: float, sy: float | None = None) -> "Affine":
        sy = sx if sy is None else sy
        return cls(matrix=((sx, 0.0), (0.0, sy)))

    @classmethod
    def shear(cls, shx: float = 0.0, shy: float = 0.0) -> "Affine":
        return cls(matrix=((1.0, shx), (shy, 1.0)))

    @classmethod
    def translation(cls, dx: float, dy: float) -> "Affine":
        return cls(translate=(dx, dy))

    def then(self, other: "Affine") -> "Affine":
        """Compose: apply self first, then other (other @ self)."""
        return Affine(matrix=other.A @ self.A,
                      translate=other.A @ self.t + other.t)

    # -------------------------------------------------------------------
    def run(self, cloud: PointCloud) -> PointCloud:
        out = cloud.copy()
        out.xy = cloud.xy @ self.A.T + self.t

        # Rotational part: how a unit +x direction vector turns under A
        if "angle" in out.attrs:
            ang = out.attrs["angle"]
            dirs = np.stack([np.cos(ang), np.sin(ang)], axis=1) @ self.A.T
            out.attrs["angle"] = np.arctan2(dirs[:, 1], dirs[:, 0])

        # Mean isotropic scale: sqrt(|det A|)
        s = math.sqrt(abs(np.linalg.det(self.A)))
        if abs(s - 1.0) > 1e-12:
            for key in ("size",):
                if key in out.attrs:
                    out.attrs[key] = out.attrs[key] * s
        return out


class PolarWarp(Node):
    """Map a cartesian cloud into polar space: a well-posed nonlinear map.

    Treats x as arc position and y as radial position:

        r     = radius_fn(y)
        theta = x / r_ref  (arc length preserved at reference radius)

    This lets any cartesian tessellation be bent into a circular pattern.
    Point angles are rotated to follow the local outward normal.
    """

    def __init__(self, r_offset: float = 0.0):
        super().__init__(r_offset=r_offset)
        self.r_offset = float(r_offset)

    def run(self, cloud: PointCloud) -> PointCloud:
        out = cloud.copy()
        r = cloud.y + self.r_offset
        r_ref = np.maximum(np.abs(r), 1e-9)
        theta = cloud.x / r_ref
        out.xy = np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)
        if "angle" in out.attrs:
            out.attrs["angle"] = out.attrs["angle"] + theta
        else:
            out.set("angle", theta)
        return out


# ----------------------------------------------------------------------
# Attribute modulation (image / field convolution)
# ----------------------------------------------------------------------

def _sample_region(cloud: PointCloud, region):
    """Resolve the world rectangle mapped to the field's unit square.

    region=None        : the cloud's bounding box (data-driven).
    region="symmetric" : origin-centered box covering the cloud. Radial
        clouds rarely have a sample point at exactly angle pi, so their
        raw bbox — and hence the field center — sits slightly off the
        true pattern center; this keeps the field centered on the origin.
    region=(xmin, ymin, xmax, ymax) : explicit.
    """
    if region == "symmetric":
        hw = max(float(np.max(np.abs(cloud.x))), 1e-9)
        hh = max(float(np.max(np.abs(cloud.y))), 1e-9)
        return -hw, -hh, hw, hh
    if region is not None:
        return region
    xmin, ymin = cloud.xy.min(axis=0)
    xmax, ymax = cloud.xy.max(axis=0)
    return xmin, ymin, xmax, ymax


class FieldModulate(Node):
    """Drive a point attribute from a Field sampled at each point.

    The cloud's bounding box (or ``region="symmetric"`` for an
    origin-centered box, or an explicit ``region=(xmin, ymin, xmax,
    ymax)``) is mapped to the field's unit square; the sampled value
    ``f`` in [0, 1] then updates the attribute:

        mode="multiply":  attr *= lo + (hi - lo) * f
        mode="set":       attr  = lo + (hi - lo) * f
        mode="add":       attr += lo + (hi - lo) * f

    Typical uses:
        FieldModulate("scale", ImageField("logo.png", invert=True))
            — cutouts grow where the logo is dark
        FieldModulate("angle", field, lo=0, hi=3.14, mode="add")
            — cutouts rotate along a gradient

    The ``scale`` attribute is a dimensionless multiplier consumed by
    decorators on top of ``size``; it is created (default 1.0) if absent.
    """

    def __init__(self, attr: str, field: Field, lo: float = 0.0,
                 hi: float = 1.0, mode: str = "multiply", region=None):
        super().__init__(attr=attr, lo=lo, hi=hi, mode=mode, region=region)
        self.attr = attr
        self.field = field
        self.lo, self.hi = float(lo), float(hi)
        self.mode = mode
        self.region = region

    def run(self, cloud: PointCloud, field: Field | None = None) -> PointCloud:
        field = field if field is not None else self.field
        if len(cloud) == 0:
            return cloud

        xmin, ymin, xmax, ymax = _sample_region(cloud, self.region)
        w = max(xmax - xmin, 1e-9)
        h = max(ymax - ymin, 1e-9)

        uv = np.stack([(cloud.x - xmin) / w, (cloud.y - ymin) / h], axis=1)
        f = np.clip(field.sample(uv), 0.0, 1.0)
        value = self.lo + (self.hi - self.lo) * f

        out = cloud.copy()
        if self.attr == "scale" and "scale" not in out.attrs:
            out.set("scale", np.ones(len(out)))
        current = out.get(self.attr, 0.0 if self.mode == "add" else 1.0)

        if self.mode == "multiply":
            out.set(self.attr, current * value)
        elif self.mode == "set":
            out.set(self.attr, value)
        elif self.mode == "add":
            out.set(self.attr, current + value)
        else:
            raise ValueError(f"unknown mode {self.mode!r}")
        return out


class DensityWarp(Node):
    """Convolve a field with the *grid spacing*: remap point coordinates
    so the local point density follows the field.

    The mapping is separable and well-posed: along each axis, the field's
    marginal density is integrated into a CDF, whose inverse remaps the
    normalized coordinates. Where the field is strong the grid compresses
    (denser points, smaller local pitch); where it is weak the grid
    stretches. The overall bounding box is preserved.

    strength : 0 = no effect, 1 = full density mapping (blends linearly).
    axes     : "both", "x", or "y".
    region   : field mapping region — None (bbox), "symmetric"
        (origin-centered box), or explicit (xmin, ymin, xmax, ymax);
        see :func:`_sample_region`.
    The per-point ``size`` attribute is rescaled by the local compression
    factor so decorated cutouts shrink/grow with the local spacing. The
    factor is the *minimum* of the two axis scales: when only one axis
    stretches (e.g. a linear gradient), the other axis's pitch is
    unchanged, and any size growth beyond it would make neighboring
    cutouts overlap across that axis.
    """

    def __init__(self, field: Field, strength: float = 1.0,
                 axes: str = "both", resolution: int = 256, region=None):
        super().__init__(strength=strength, axes=axes, region=region)
        self.field = field
        self.strength = float(strength)
        self.axes = axes
        self.resolution = int(resolution)
        self.region = region

    def run(self, cloud: PointCloud, field: Field | None = None) -> PointCloud:
        field = field if field is not None else self.field
        if len(cloud) == 0 or self.strength <= 0:
            return cloud

        xmin, ymin, xmax, ymax = _sample_region(cloud, self.region)
        w = max(xmax - xmin, 1e-9)
        h = max(ymax - ymin, 1e-9)

        n = self.resolution
        grid = np.linspace(0.0, 1.0, n)
        gu, gv = np.meshgrid(grid, grid, indexing="ij")
        uv_grid = np.stack([gu.ravel(), gv.ravel()], axis=1)
        density = np.clip(field.sample(uv_grid), 0.0, None).reshape(n, n)
        density = density + 1e-6  # keep the CDF strictly increasing

        u = (cloud.x - xmin) / w
        v = (cloud.y - ymin) / h
        new_u, su = self._remap(u, density.mean(axis=1), grid) \
            if self.axes in ("both", "x") else (u, np.ones_like(u))
        new_v, sv = self._remap(v, density.mean(axis=0), grid) \
            if self.axes in ("both", "y") else (v, np.ones_like(v))

        out = cloud.copy()
        out.xy = np.stack([xmin + new_u * w, ymin + new_v * h], axis=1)
        if "size" in out.attrs:
            # Local compression factor: the tighter of the two axis
            # scales. A geometric mean would inflate sizes past the
            # unstretched axis's pitch and overlap neighbors there.
            out.attrs["size"] = out.attrs["size"] * np.minimum(su, sv)
        return out

    def _remap(self, t: np.ndarray, marginal: np.ndarray, grid: np.ndarray):
        """Remap normalized coords through the inverse CDF of a marginal
        density; returns (new_coords, local_scale_factor)."""
        cdf = np.cumsum(marginal)
        cdf = (cdf - cdf[0]) / (cdf[-1] - cdf[0])  # normalized, 0..1
        # High density regions should attract points: map uniform t
        # through the inverse CDF.
        warped = np.interp(t, cdf, grid)
        new_t = (1 - self.strength) * t + self.strength * warped
        # Local scale = d(new_t)/d(t): estimate from the mapping slope
        eps = 1e-4
        lo = np.clip(t - eps, 0.0, 1.0)
        hi = np.clip(t + eps, 0.0, 1.0)
        w_lo = (1 - self.strength) * lo + self.strength * np.interp(lo, cdf, grid)
        w_hi = (1 - self.strength) * hi + self.strength * np.interp(hi, cdf, grid)
        scale = (w_hi - w_lo) / np.maximum(hi - lo, 1e-9)
        return new_t, np.maximum(scale, 1e-6)


class AttributeFilter(Node):
    """Drop points whose attribute falls outside [lo, hi].

    E.g. ``AttributeFilter("scale", lo=0.05)`` removes points that an
    image field has modulated to (near) zero, so fully-dark regions of a
    logo produce no cutout at all.
    """

    def __init__(self, attr: str, lo: float = -np.inf, hi: float = np.inf):
        super().__init__(attr=attr, lo=lo, hi=hi)
        self.attr, self.lo, self.hi = attr, float(lo), float(hi)

    def run(self, cloud: PointCloud) -> PointCloud:
        vals = cloud.get(self.attr, 1.0).astype(float)
        return cloud.filter((vals >= self.lo) & (vals <= self.hi))


class TagFilter(Node):
    """Keep only points with the given tag(s)."""

    def __init__(self, *tags: str):
        super().__init__(tags=tags)
        self.tags = set(tags)

    def run(self, cloud: PointCloud) -> PointCloud:
        tags = cloud.get("tag", "default")
        mask = np.array([t in self.tags for t in tags])
        return cloud.filter(mask)


class Merge(Node):
    """Concatenate multiple PointClouds (for multi-generator graphs)."""

    def run(self, *clouds: PointCloud) -> PointCloud:
        if not clouds:
            return PointCloud.empty()
        out = clouds[0]
        for c in clouds[1:]:
            out = out.concat(c)
        return out
