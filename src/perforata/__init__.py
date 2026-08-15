"""perforata — parametric perforation pattern generator.

A node-graph based engine for generating manufacturable cutout patterns
(fan grills, vent panels, decorative screens) as DXF/SVG files.

Architecture (a directed acyclic graph of processing steps):

    Generators  -> produce a PointCloud (grid centers with attributes)
    Fields      -> scalar fields over 2D space (images, gradients, math)
    Modifiers   -> transform the PointCloud (affine ops, field modulation)
    Decorators  -> instance cutout shapes onto points (per-tag rules)
    Ops         -> geometry post-processing (crop, filters, fit-to-size)
    Exporters   -> DXF / SVG output

The legacy single-file MVP lives in the repository's ``mvp/`` folder.
"""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("perforata")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0.dev0"

from .pointcloud import PointCloud
from .graph import Node, Pipeline, Graph

__all__ = ["PointCloud", "Node", "Pipeline", "Graph", "__version__"]
