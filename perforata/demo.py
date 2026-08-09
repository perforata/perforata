"""Demo gallery: render every factory preset into one matrix image.

The modern equivalent of the MVP's ``--demo`` flag (which drew a 3x5
grid of interesting configurations to ``demo_grid.png``). Factory
presets are stored as UI state (widget key -> value dicts, see
:mod:`perforata.factory_presets`); this module interprets that state
*headlessly* — no Streamlit required — building the same node pipeline
the app would, and lays each result out on a matplotlib grid.

Defaults here must mirror the widget defaults in ``app.py`` so a preset
that omits a key renders identically in the gallery and in the UI.

Regenerate the README gallery image with::

    uv run python -m perforata.demo docs/demo_gallery.png
"""

from __future__ import annotations

import io
import math

from . import presets
from .decorators import ShapeInstancer, ShapeSpec
from .fields import (Expression, LinearGradient, RadialGradient,
                     ShapeGradient, TextField)
from .generators import CartesianGrid, ConcentricRings, HexGrid
from .modifiers import (Affine, AttributeFilter, DensityWarp, FieldModulate,
                        PolarWarp)
from .ops import Boundary, Crop, cutouts_only, resolve_constraints
from .render import plot_shapes


def _field(state: dict, key: str = "fconv"):
    """Build a Field from UI state (mirrors app.field_source_controls)."""
    src = state.get(f"{key}_src", "Shape gradient")
    if src == "Text / letter":
        text = state.get(f"{key}_txt", "A").strip()
        if not text:
            return None
        return TextField(text,
                         invert=not state.get(f"{key}_txtinv", True),
                         bold=state.get(f"{key}_txtbold", True),
                         blur=state.get(f"{key}_txtblur", 0.0))
    if src == "Shape gradient":
        return ShapeGradient(shape=state.get(f"{key}_gshape", "circle"),
                             falloff=state.get(f"{key}_gfall", "exponential"),
                             k=state.get(f"{key}_gk", 3.0),
                             invert=state.get(f"{key}_ginv", True))
    if src == "Linear gradient":
        return LinearGradient(angle_deg=state.get(f"{key}_ang", 0.0))
    if src == "Radial gradient":
        return RadialGradient(invert=state.get(f"{key}_radinv", True))
    if src == "Expression":
        return Expression(state.get(f"{key}_expr",
                                    "0.5 + 0.5*sin(6.283*u)"))
    return None  # "Uploaded image" cannot be restored headlessly


def _generator(state: dict, key: str = "gen"):
    """Build the generator (mirrors app.generator_controls)."""
    grid_type = state.get(f"{key}_type", "Cartesian")
    tag_mode = state.get(f"{key}_tagmode", "Uniform")
    alternate = tag_mode.startswith("Alternate")
    major_every = (int(state.get(f"{key}_majn", 2))
                   if tag_mode.startswith("Major") else 0)

    if grid_type == "Cartesian":
        gen = CartesianGrid(
            pitch_x=state.get(f"{key}_px", 10.0),
            pitch_y=state.get(f"{key}_py", 10.0),
            width=state.get(f"{key}_w", 200.0),
            height=state.get(f"{key}_h", 150.0),
            stagger=state.get(f"{key}_stag", 0.0),
            col_stagger=state.get(f"{key}_cstag", 0.0),
            major_every=major_every, alternate=alternate,
            fit=state.get(f"{key}_fit", False))
    elif grid_type == "Hexagonal":
        gen = HexGrid(
            pitch=state.get(f"{key}_p", 10.0),
            width=state.get(f"{key}_w", 200.0),
            height=state.get(f"{key}_h", 150.0),
            major_every=major_every, alternate=alternate,
            fit=state.get(f"{key}_fit", False))
    else:
        gen = ConcentricRings(
            rings=int(state.get(f"{key}_r", 15)),
            r0=state.get(f"{key}_r0", 10.0),
            factor=state.get(f"{key}_f", 1.15),
            mode=state.get(f"{key}_mode", "exponential"),
            twist=state.get(f"{key}_tw", 0.0),
            spokes=int(state.get(f"{key}_sp", 0)),
            stagger=state.get(f"{key}_st", 0.0),
            invert=state.get(f"{key}_inv", False),
            major_every=major_every, alternate=alternate)
    return gen, alternate, major_every


def _modifiers(state: dict) -> list:
    """Build the Simple-mode modifier list (field convolution +
    transforms) in the same order the app composes them."""
    mods: list = []
    if state.get("fconv_on", False):
        field = _field(state)
        if field is not None:
            target = state.get("fconv_target", "Cutout size")
            if target in ("Grid spacing (density)", "Both"):
                mods.append(DensityWarp(
                    field, strength=state.get("fconv_dstr", 0.8),
                    region="symmetric"))
            if target in ("Cutout size", "Both"):
                mods.append(FieldModulate(
                    "scale", field,
                    lo=state.get("fconv_lo", 0.15),
                    hi=state.get("fconv_hi", 1.0),
                    region="symmetric"))
                drop = state.get("fconv_drop", 0.1)
                if drop > 0:
                    mods.append(AttributeFilter("scale", lo=drop))
    rot = state.get("xform_rot", 0.0)
    if abs(rot) > 1e-9:
        mods.insert(0, Affine.rotation(rot))
    if state.get("xform_polar", False):
        mods.insert(0, PolarWarp(r_offset=state.get("xform_roff", 100.0)))
    return mods


def _spec(state: dict, tag: str, default_shape: str,
          default_rot: float = 0.0) -> ShapeSpec:
    key = f"rules_{tag}"
    return ShapeSpec(shape=state.get(f"{key}_shape", default_shape),
                     fill=state.get(f"{key}_fill", 0.7),
                     rotation=state.get(f"{key}_rot", default_rot),
                     absolute=state.get(f"{key}_abs", False))


def _rules(state: dict, alternate: bool, major_every: int) -> dict:
    if alternate:
        return {"even": _spec(state, "even", "triangle", 90.0),
                "odd": _spec(state, "odd", "triangle", -90.0)}
    if major_every > 0:
        return {"major": _spec(state, "major", "hexagon"),
                "minor": _spec(state, "minor", "circle")}
    return {"*": _spec(state, "all", "hexagon")}


def _crop(state: dict, key: str = "mfg_crop"):
    if not state.get(f"{key}_on", False):
        return None
    if state.get(f"{key}_kind", "Rectangle") == "Rectangle":
        boundary = Boundary.rect(state.get(f"{key}_w", 200.0),
                                 state.get(f"{key}_h", 150.0))
    else:
        boundary = Boundary.circle(state.get(f"{key}_d", 120.0))
    return Crop(boundary,
                mode=state.get(f"{key}_mode", "cull"),
                margin=state.get(f"{key}_m", 0.0),
                include_outline=state.get(f"{key}_out", True))


def run_state(state: dict) -> list[dict]:
    """Run a preset's UI-state dict through the full pipeline headlessly;
    returns the finished cutout shape list."""
    gen, alternate, major_every = _generator(state)
    cloud = gen.run()
    for mod in _modifiers(state):
        cloud = mod.run(cloud)
    shapes = ShapeInstancer(rules=_rules(state, alternate, major_every))\
        .run(cloud)
    shapes = resolve_constraints(shapes,
                                 diameter=state.get("mfg_fit", 0.0),
                                 min_wall=state.get("mfg_minw", 0.0),
                                 min_hole=state.get("mfg_minh", 0.0))
    crop = _crop(state)
    if crop is not None:
        shapes = crop.run(shapes)
    return cutouts_only(shapes)


def render_gallery(names: list[str] | None = None, cols: int = 4,
                   panel_size: float = 4.5):
    """Render each named factory preset (default: all) onto one
    matplotlib figure — the demo matrix. Returns the figure."""
    import matplotlib.pyplot as plt

    if names is None:
        names = presets.list_factory()
    rows = max(1, math.ceil(len(names) / cols))
    fig, axes = plt.subplots(rows, cols,
                             figsize=(cols * panel_size,
                                      rows * panel_size + 0.6),
                             squeeze=False)
    for ax in axes.flat:
        ax.set_axis_off()
    for ax, name in zip(axes.flat, names):
        state = presets.load_factory(name)["ui_state"]
        cuts = run_state(state)
        ax.set_axis_on()
        plot_shapes(cuts, ax=ax, linewidth=0.5, title=name)
    fig.suptitle("perforata demo gallery — factory presets", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


def gallery_png(names: list[str] | None = None, cols: int = 4,
                dpi: int = 100) -> bytes:
    """Render the demo gallery to PNG bytes (for the UI demo button)."""
    import matplotlib.pyplot as plt

    fig = render_gallery(names, cols=cols)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_gallery.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gallery_png())
    print(f"Saved demo gallery to {out}")
