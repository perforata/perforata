"""Optional-dependency guard.

The core engine depends only on numpy; everything else is an extra.
Call :func:`require` before importing an optional module so a missing
dependency fails with an actionable message instead of a bare
``ModuleNotFoundError``.
"""

from __future__ import annotations

import importlib

#: extra name -> human description (kept in sync with pyproject.toml)
EXTRAS = {
    "geo": "shapely-based crop slicing and edge-clearance checks",
    "dxf": "DXF export",
    "raster": "text/image fields (pillow)",
    "render": "matplotlib previews and the demo gallery",
    "app": "the Streamlit UI",
}


def require(module: str, extra: str):
    """Import ``module``, or raise an ImportError that names the extra
    to install (``pip install perforata[<extra>]``)."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        detail = EXTRAS.get(extra, extra)
        raise ImportError(
            f"{module!r} is required for {detail}. "
            f"Install it with: pip install perforata[{extra}]"
        ) from exc
