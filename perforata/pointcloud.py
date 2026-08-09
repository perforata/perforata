"""PointCloud: the core datatype flowing through the node graph.

A PointCloud is a set of 2D points plus named per-point attribute arrays.
Generators emit them, modifiers transform them, and decorators consume
them to instance actual cutout geometry.

Standard attributes (all optional, but generators should provide them):

    angle   : float, orientation in radians (e.g. local outward normal on
              radial grids, 0 on cartesian grids). Decorators add their
              own rotation on top of this.
    size    : float, the local characteristic length (ring gap or grid
              pitch). Cutout sizes are usually ``size * fill``.
    tag     : str, a label used by decorators to select shape rules
              (e.g. "major"/"minor", "even"/"odd", "default").

Generator-specific attributes like ``row``, ``col`` or ``ring`` are also
carried along so downstream nodes can key off them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class PointCloud:
    """A set of 2D points with named per-point attributes."""

    xy: np.ndarray                       # shape (N, 2), float
    attrs: dict = field(default_factory=dict)  # name -> np.ndarray of len N

    def __post_init__(self):
        self.xy = np.asarray(self.xy, dtype=float).reshape(-1, 2)
        for k, v in list(self.attrs.items()):
            v = np.asarray(v)
            if v.shape[0] != len(self):
                raise ValueError(
                    f"attribute {k!r} has length {v.shape[0]}, "
                    f"expected {len(self)}"
                )
            self.attrs[k] = v

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return self.xy.shape[0]

    @property
    def x(self) -> np.ndarray:
        return self.xy[:, 0]

    @property
    def y(self) -> np.ndarray:
        return self.xy[:, 1]

    # ------------------------------------------------------------------
    def get(self, name: str, default=None) -> np.ndarray:
        """Attribute array by name; a filled default array if missing."""
        if name in self.attrs:
            return self.attrs[name]
        if default is None:
            raise KeyError(f"PointCloud has no attribute {name!r}")
        return np.full(len(self), default)

    def set(self, name: str, values) -> "PointCloud":
        """Set an attribute array (accepts scalars, broadcasts). Returns self."""
        arr = np.asarray(values)
        if arr.ndim == 0:
            arr = np.full(len(self), arr[()])
        if arr.shape[0] != len(self):
            raise ValueError(
                f"attribute {name!r} has length {arr.shape[0]}, expected {len(self)}"
            )
        self.attrs[name] = arr
        return self

    def copy(self) -> "PointCloud":
        return PointCloud(self.xy.copy(),
                          {k: v.copy() for k, v in self.attrs.items()})

    def filter(self, mask) -> "PointCloud":
        """New PointCloud containing only points where mask is True."""
        mask = np.asarray(mask, dtype=bool)
        return PointCloud(self.xy[mask],
                          {k: v[mask] for k, v in self.attrs.items()})

    def concat(self, other: "PointCloud") -> "PointCloud":
        """Concatenate two clouds. Attributes missing on one side are
        filled with zeros / empty strings so the result stays rectangular."""
        keys = set(self.attrs) | set(other.attrs)
        attrs = {}
        for k in keys:
            a = self.attrs.get(k)
            b = other.attrs.get(k)
            if a is None:
                a = _fill_like(b, len(self))
            if b is None:
                b = _fill_like(a, len(other))
            attrs[k] = np.concatenate([a, b])
        return PointCloud(np.vstack([self.xy, other.xy]), attrs)

    # ------------------------------------------------------------------
    @staticmethod
    def empty() -> "PointCloud":
        return PointCloud(np.zeros((0, 2)))


def _fill_like(template: np.ndarray, n: int) -> np.ndarray:
    """Neutral fill values matching a template array's dtype."""
    if template.dtype.kind in ("U", "S", "O"):
        return np.full(n, "", dtype=template.dtype)
    return np.zeros(n, dtype=template.dtype)
