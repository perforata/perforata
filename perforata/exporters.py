"""Exporters: write final shape lists to manufacturable file formats.

All exporters can either write to a path or return the file content as
bytes (for the Streamlit download button).
"""

from __future__ import annotations

import io

from .graph import Node
from .decorators import shapes_bounds


class DXFExporter(Node):
    """Emit native DXF CIRCLE and closed LWPOLYLINE entities."""

    def __init__(self, path: str | None = None, layer: str = "CUTS"):
        super().__init__(path=path, layer=layer)
        self.path = path
        self.layer = layer

    def run(self, shapes: list[dict]):
        import ezdxf
        doc = ezdxf.new("R2010")
        if self.layer != "0":
            doc.layers.add(self.layer)
        msp = doc.modelspace()
        attribs = {"layer": self.layer}
        for s in shapes:
            if s["type"] == "circle":
                msp.add_circle(s["center"], s["radius"], dxfattribs=attribs)
            else:
                msp.add_lwpolyline(s["points"], close=True, dxfattribs=attribs)
        if self.path:
            doc.saveas(self.path)
            return self.path
        buf = io.StringIO()
        doc.write(buf)
        return buf.getvalue().encode("utf-8")


class SVGExporter(Node):
    """Emit an SVG with one path/circle per cutout.

    ``unit`` is stamped on the width/height attributes ("mm" by default)
    so downstream tools import at true physical scale.
    """

    def __init__(self, path: str | None = None, unit: str = "mm",
                 stroke: str = "black", stroke_width: float = 0.2):
        super().__init__(path=path, unit=unit)
        self.path = path
        self.unit = unit
        self.stroke = stroke
        self.stroke_width = stroke_width

    def run(self, shapes: list[dict]):
        if shapes:
            x0, y0, x1, y1 = shapes_bounds(shapes)
        else:
            x0 = y0 = 0.0
            x1 = y1 = 1.0
        pad = 0.02 * max(x1 - x0, y1 - y0, 1e-9)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        w, h = x1 - x0, y1 - y0

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{w:.3f}{self.unit}" height="{h:.3f}{self.unit}" '
            f'viewBox="{x0:.3f} {-y1:.3f} {w:.3f} {h:.3f}">',
            f'<g fill="none" stroke="{self.stroke}" '
            f'stroke-width="{self.stroke_width}">',
        ]
        # SVG's y axis points down; negate y to preserve orientation.
        for s in shapes:
            if s["type"] == "circle":
                cx, cy = s["center"]
                parts.append(f'<circle cx="{cx:.4f}" cy="{-cy:.4f}" '
                             f'r="{s["radius"]:.4f}"/>')
            else:
                d = " ".join(f"{x:.4f},{-y:.4f}" for x, y in s["points"])
                parts.append(f'<polygon points="{d}"/>')
        parts.append("</g></svg>")
        data = "\n".join(parts).encode("utf-8")

        if self.path:
            with open(self.path, "wb") as f:
                f.write(data)
            return self.path
        return data
