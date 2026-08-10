"""perforata — Streamlit UI.

Interactive front-end for the node-graph pattern engine. Run with:

    uv run streamlit run app.py

The app is organized around **pipelines**: at any moment a named pipeline
is selected (shown at the top of the sidebar). On startup a default
factory pipeline is loaded. Editing any control marks the pipeline as
*modified* (●); you can save it under a name (stored in ``presets/user/``,
git-ignored), revert to the loaded state, share it as a ``.pfp`` file,
or load another pipeline.

Both editor modes drive the same underlying node pipeline
(generator -> modifiers -> decorator -> constraint ops -> export):
* Simple   — grid + cutouts + field convolution + manufacturing.
* Advanced — a freely composable, ordered stack of modifier steps.
"""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
import streamlit as st

from perforata.generators import CartesianGrid, HexGrid, ConcentricRings
from perforata.fields import (ImageField, TextField, LinearGradient,
                              RadialGradient, ShapeGradient, Expression)
from perforata.modifiers import (Affine, FieldModulate, AttributeFilter,
                                 DensityWarp, TagFilter, PolarWarp)
from perforata.decorators import ShapeInstancer, ShapeSpec, shapes_bounds
from perforata.ops import (Boundary, Crop, resolve_constraints,
                           cutouts_only, min_spacing, edge_clearance)
from perforata.exporters import DXFExporter, SVGExporter
from perforata.render import plot_shapes, plot_pointcloud
from perforata import presets


@st.cache_data(show_spinner=False)
def demo_gallery_png() -> bytes:
    """Cached demo-gallery matrix of all factory presets (see
    perforata.demo). Cache key is implicit: factory presets only change
    with the code, and Streamlit clears the cache on source change."""
    from perforata.demo import gallery_png
    return gallery_png()

st.set_page_config(page_title="perforata", layout="wide")

SHAPES = ["circle", "triangle", "square", "pentagon", "hexagon"]
DEFAULT_PIPELINE = "honeycomb-vent"

# Session-state keys with these prefixes (plus "adv_steps") define the
# pipeline; they are snapshotted into presets and diffed for the
# "modified" indicator. File-uploader keys (*_img) are excluded — they
# cannot be restored programmatically.
STATE_PREFIXES = ("mode_", "gen_", "fconv_", "xform_", "rules_", "mfg_",
                  "step")


# ----------------------------------------------------------------------
# Pipeline state: snapshot / hydrate / dirty tracking
# ----------------------------------------------------------------------

def _is_pipeline_key(k) -> bool:
    return k == "adv_steps" or (isinstance(k, str)
                                and k.startswith(STATE_PREFIXES)
                                and not k.endswith("_img"))


def snapshot_state() -> dict:
    """Snapshot all pipeline-defining widget values."""
    return {k: v for k, v in st.session_state.items() if _is_pipeline_key(k)}


def stage_load(state: dict, name: str, rerun: bool = True):
    """Queue a pipeline's UI state to be applied on the next run
    (widget keys can't be overwritten mid-render)."""
    st.session_state["_pending_state"] = dict(state)
    st.session_state["_pending_name"] = name
    if rerun:
        st.rerun()


def _apply_pending():
    if "_pending_state" not in st.session_state:
        return
    state = st.session_state.pop("_pending_state")
    name = st.session_state.pop("_pending_name")
    # Clear existing pipeline keys, then hydrate the loaded ones.
    for k in [k for k in st.session_state if _is_pipeline_key(k)]:
        del st.session_state[k]
    for k, v in state.items():
        if not str(k).endswith("_img"):
            st.session_state[k] = v
    st.session_state["pipeline_name"] = name
    # The full baseline (including widget defaults for keys the preset
    # omitted) is captured at the end of the next render.
    st.session_state["baseline"] = None


def is_dirty() -> bool:
    """True if any baseline value differs from the current widget state.
    Keys absent on either side are ignored (widgets appear/disappear as
    structural toggles change; the toggles themselves are compared)."""
    baseline = st.session_state.get("baseline")
    if not baseline:
        return False
    snap = snapshot_state()
    return any(k in snap and snap[k] != baseline[k] for k in baseline)


def describe_pipeline(gen, modifiers, rules, crop,
                      min_hole, min_wall, target_d,
                      cloud=None, cuts=None) -> str:
    """Human-readable text dump of the current configuration — the UI
    widget state plus the constructed node pipeline — for debugging and
    bug reports."""
    lines = [
        "# perforata pipeline debug dump",
        f"pipeline: {st.session_state.get('pipeline_name', 'untitled')}"
        f"{' (modified)' if is_dirty() else ''}",
        "",
        "## Nodes",
        f"generator: {gen!r}",
    ]
    for i, m in enumerate(modifiers):
        lines.append(f"modifier[{i}]: {m!r}")
    for tag, spec in rules.items():
        lines.append(f"rule[{tag!r}]: {spec!r}")
    lines.append(f"crop: {crop!r}")
    lines.append(f"constraints: min_hole={min_hole} min_wall={min_wall} "
                 f"target_d={target_d}")
    if cloud is not None:
        lines += ["", "## Result", f"points: {len(cloud)}"]
    if cuts:
        sizes = [s["size"] for s in cuts]
        lines.append(f"cutouts: {len(cuts)} "
                     f"(hole sizes {min(sizes):.3f}..{max(sizes):.3f})")
        x0, y0, x1, y1 = shapes_bounds(cuts)
        lines.append(f"extent: {x1 - x0:.3f} x {y1 - y0:.3f} "
                     f"(bounds {x0:.3f},{y0:.3f} .. {x1:.3f},{y1:.3f})")
    lines += ["", "## UI state (widget keys)",
              json.dumps(snapshot_state(), indent=2, sort_keys=True,
                         default=repr)]
    return "\n".join(lines) + "\n"


# Apply any queued pipeline load before rendering widgets.
_apply_pending()

# First run: select the default factory pipeline.
if "pipeline_name" not in st.session_state:
    payload = presets.load_factory(DEFAULT_PIPELINE)
    stage_load(payload["ui_state"], DEFAULT_PIPELINE, rerun=False)
    _apply_pending()


# ----------------------------------------------------------------------
# Sidebar widget helpers (all keys carry pipeline-state prefixes)
# ----------------------------------------------------------------------

def shape_spec_controls(label: str, key: str, default_shape="hexagon",
                        default_rot=0.0) -> ShapeSpec:
    with st.expander(label, expanded=True):
        shape = st.selectbox("Shape", SHAPES, key=f"{key}_shape",
                             index=SHAPES.index(default_shape),
                             help="Polygon cut at each grid point.")
        fill = st.slider("Fill (size vs pitch)", 0.05, 1.0, 0.7, 0.05,
                         key=f"{key}_fill",
                         help="Cutout size as a fraction of the local grid "
                              "pitch — at 1.0 neighboring cutouts touch.")
        rot = st.slider("Rotation (deg)", -180.0, 180.0, default_rot, 5.0,
                        key=f"{key}_rot",
                        help="Extra rotation applied to every cutout.")
        absolute = st.checkbox("Global orientation (ignore local normal)",
                               key=f"{key}_abs",
                               help="Keep all cutouts aligned to the canvas "
                                    "axes instead of following the local "
                                    "grid direction (e.g. ring tangents).")
    return ShapeSpec(shape=shape, fill=fill, rotation=rot, absolute=absolute)


def generator_controls(key: str = "gen"):
    grid_type = st.selectbox(
        "Grid type", ["Cartesian", "Hexagonal", "Concentric rings"],
        key=f"{key}_type",
        help="Lattice used to place the cutout centers.")

    tag_mode = st.selectbox(
        "Row tagging",
        ["Uniform", "Alternate rows (even/odd)", "Major every Nth row"],
        key=f"{key}_tagmode",
        help="Tags let different rows/rings receive different cutouts — "
             "e.g. triangles flipping orientation row by row.")
    major_every = 0
    alternate = False
    if tag_mode.startswith("Alternate"):
        alternate = True
    elif tag_mode.startswith("Major"):
        major_every = st.number_input("Major every N", 2, 20, 2,
                                      key=f"{key}_majn",
                                      help="Every Nth row/ring is tagged "
                                           "'major' and can receive a "
                                           "different cutout.")

    if grid_type == "Cartesian":
        c1, c2 = st.columns(2)
        pitch_x = c1.number_input("Pitch X", 1.0, 100.0, 10.0,
                                  key=f"{key}_px",
                                  help="Center-to-center spacing along X.")
        pitch_y = c2.number_input("Pitch Y", 1.0, 100.0, 10.0,
                                  key=f"{key}_py",
                                  help="Center-to-center spacing along Y.")
        c1, c2 = st.columns(2)
        width = c1.number_input("Width", 10.0, 2000.0, 200.0,
                                key=f"{key}_w",
                                help="Overall pattern width.")
        height = c2.number_input("Height", 10.0, 2000.0, 150.0,
                                 key=f"{key}_h",
                                 help="Overall pattern height.")
        c1, c2 = st.columns(2)
        stagger = c1.slider("Row stagger (fraction of pitch)",
                            0.0, 0.9, 0.0, 0.05, key=f"{key}_stag",
                            help="Shift alternate rows sideways by this "
                                 "fraction of the X pitch (0.5 = brick "
                                 "bond).")
        col_stagger = c2.slider("Column stagger (fraction of pitch)",
                                0.0, 0.9, 0.0, 0.05, key=f"{key}_cstag",
                                help="Shift alternate columns vertically "
                                     "by this fraction of the Y pitch.")
        fit = st.checkbox(
            "Fit grid to panel (balance edge margins)",
            key=f"{key}_fit",
            help="Snaps each pitch so a whole number of cells spans the "
                 "panel and centers the lattice — the outermost holes sit "
                 "half a pitch from every edge instead of leaving uneven "
                 "top/bottom vs left/right gaps.")
        gen = CartesianGrid(pitch_x=pitch_x, pitch_y=pitch_y,
                            width=width, height=height, stagger=stagger,
                            col_stagger=col_stagger,
                            major_every=major_every, alternate=alternate,
                            fit=fit)
        if fit:
            st.caption(f"Fitted pitch: {gen.pitch_x:.3f} × "
                       f"{gen.pitch_y:.3f}")
    elif grid_type == "Hexagonal":
        pitch = st.number_input("Pitch", 1.0, 100.0, 10.0, key=f"{key}_p",
                                help="Center-to-center distance between "
                                     "neighboring holes.")
        c1, c2 = st.columns(2)
        width = c1.number_input("Width", 10.0, 2000.0, 200.0,
                                key=f"{key}_w",
                                help="Overall pattern width.")
        height = c2.number_input("Height", 10.0, 2000.0, 150.0,
                                 key=f"{key}_h",
                                 help="Overall pattern height.")
        fit = st.checkbox(
            "Fit grid to panel (balance edge margins)",
            key=f"{key}_fit",
            help="Snaps the horizontal and row pitches so whole cells span "
                 "the panel and centers the lattice — edge margins come out "
                 "even at the cost of slightly non-equilateral spacing.")
        gen = HexGrid(pitch=pitch, width=width, height=height,
                      major_every=major_every, alternate=alternate,
                      fit=fit)
        if fit:
            st.caption(f"Fitted pitch: {gen.pitch_x:.3f} × "
                       f"{gen.pitch_y:.3f} (rows)")
    else:
        c1, c2 = st.columns(2)
        rings = c1.number_input("Rings", 1, 60, 15, key=f"{key}_r",
                                help="Number of concentric rings.")
        r0 = c2.number_input("Inner radius r0", 0.1, 200.0, 10.0,
                             key=f"{key}_r0",
                             help="Radius of the innermost ring.")
        c1, c2 = st.columns(2)
        mode = c1.selectbox("Spacing", ["exponential", "quadratic", "linear"],
                            key=f"{key}_mode",
                            help="How the gap between successive rings "
                                 "grows with radius.")
        factor = c2.number_input("Factor", 0.01, 20.0, 1.15,
                                 key=f"{key}_f",
                                 help="Growth factor for the ring spacing "
                                      "(larger = rings spread apart "
                                      "faster).")
        c1, c2, c3 = st.columns(3)
        twist = c1.number_input("Twist (deg/ring)", -90.0, 90.0, 0.0,
                                key=f"{key}_tw",
                                help="Rotate each successive ring by this "
                                     "many degrees — creates spiral arms.")
        spokes = c2.number_input("Spokes (0=auto)", 0, 128, 0,
                                 key=f"{key}_sp",
                                 help="Fixed number of points per ring; "
                                      "0 picks a count that keeps spacing "
                                      "roughly even.")
        stagger = c3.number_input("Stagger", 0.0, 1.0, 0.0,
                                  key=f"{key}_st",
                                  help="Rotate alternate rings by this "
                                       "fraction of a spoke step so points "
                                       "interleave.")
        invert = st.checkbox("Invert density (dense outside)",
                             key=f"{key}_inv",
                             help="Flip the spacing profile so rings crowd "
                                  "toward the rim instead of the center.")
        gen = ConcentricRings(rings=int(rings), r0=r0, factor=factor,
                              mode=mode, twist=twist, spokes=int(spokes),
                              stagger=stagger, invert=invert,
                              major_every=major_every, alternate=alternate)
    return gen, alternate, major_every


def field_source_controls(key: str):
    """Pick a field source. Returns a Field or None."""
    source = st.selectbox("Field source",
                          ["Shape gradient", "Text / letter",
                           "Uploaded image", "Linear gradient",
                           "Radial gradient", "Expression"],
                          key=f"{key}_src",
                          help="What generates the 0..1 driving field: a "
                               "geometric gradient, rendered text, an "
                               "uploaded image, or a math expression.")
    field = _field_source_inner(source, key)
    if field is None:
        return None
    fscale = st.slider(
        "Field scale (vs canvas)", 0.25, 4.0, 1.0, 0.05,
        key=f"{key}_fscale",
        help="Zoom the driving geometry relative to the pattern: >1 "
             "magnifies it (features span more of the canvas), <1 "
             "shrinks it toward the center.")
    if abs(fscale - 1.0) > 1e-9:
        field = field.scaled(fscale)
    return field


def _field_source_inner(source: str, key: str):
    if source == "Text / letter":
        c1, c2 = st.columns([2, 1])
        text = c1.text_input("Text", "A", key=f"{key}_txt",
                             help="A letter, monogram, or short word "
                                  "rendered as the modulation field.")
        bold = c2.checkbox("Bold", value=True, key=f"{key}_txtbold",
                           help="Render the text with a heavier font "
                                "weight.")
        blur = st.slider("Edge softness (blur)", 0.0, 0.10, 0.0, 0.005,
                         key=f"{key}_txtblur",
                         help="0 = hard glyph edge (cutout sizes step at "
                              "the outline). Higher values feather the "
                              "edge so sizes ramp smoothly across it.")
        inv = st.checkbox("Effect inside the letter", value=True,
                          key=f"{key}_txtinv",
                          help="On: the field is strong inside the glyph "
                               "(big holes spell the letter). Off: strong "
                               "outside it (the letter appears as solid "
                               "material).")
        if not text.strip():
            st.info("Enter some text to enable this step.")
            return None
        return TextField(text.strip(), invert=not inv, bold=bold, blur=blur)
    if source == "Shape gradient":
        c1, c2 = st.columns(2)
        shape = c1.selectbox("Gradient shape",
                             ["circle", "square", "hexagon"],
                             key=f"{key}_gshape",
                             help="Contour shape the gradient radiates "
                                  "from.")
        falloff = c2.selectbox("Falloff",
                               ["exponential", "linear", "quadratic",
                                "gaussian", "sphere"],
                               key=f"{key}_gfall",
                               help="sphere = hemisphere height profile: "
                                    "with 'Strong at center' the cutouts "
                                    "shade a 3D ball resting on the panel.")
        k = 3.0
        if falloff in ("exponential", "gaussian"):
            k = st.slider("Steepness", 0.5, 10.0, 3.0, 0.5,
                          key=f"{key}_gk",
                          help="How sharply the gradient falls off — "
                               "higher = a tighter hot spot.")
        inv = st.checkbox("Strong at center", value=True,
                          key=f"{key}_ginv",
                          help="On: field peaks at the middle and fades "
                               "outward. Off: strongest at the rim.")
        return ShapeGradient(shape=shape, falloff=falloff, k=k, invert=inv)
    if source == "Uploaded image":
        up = st.file_uploader("Image (logo / letter)",
                              type=["png", "jpg", "jpeg", "bmp", "gif"],
                              key=f"{key}_img",
                              help="Image brightness becomes the field — "
                                   "high-contrast logos and glyphs work "
                                   "best.")
        if up is None:
            st.info("Upload an image to enable this step. "
                    "(Images are not restored from saved presets.)")
            return None
        from PIL import Image
        invert = st.checkbox("Invert (dark ink = strong effect)",
                             value=True, key=f"{key}_imginv",
                             help="On: dark pixels drive the effect (good "
                                  "for black-on-white artwork). Off: "
                                  "bright pixels drive it.")
        return ImageField(Image.open(up), invert=invert)
    if source == "Linear gradient":
        angle = st.slider("Gradient angle (deg)", 0.0, 360.0, 0.0, 15.0,
                          key=f"{key}_ang",
                          help="Direction the ramp runs: 0° = left to "
                               "right, 90° = bottom to top.")
        return LinearGradient(angle_deg=angle)
    if source == "Radial gradient":
        inv = st.checkbox("Bright center", value=True, key=f"{key}_radinv",
                          help="On: field peaks at the middle and fades "
                               "outward. Off: strongest at the rim.")
        return RadialGradient(invert=inv)
    expr = st.text_input("Expression of u, v (0..1)",
                         "0.5 + 0.5*sin(6.283*u)", key=f"{key}_expr",
                         help="Python-style math in u, v (canvas coords, "
                              "0..1) returning field strength — sin, cos, "
                              "exp, sqrt, abs, min, max are available.")
    try:
        return Expression(expr)
    except SyntaxError:
        st.error("Invalid expression")
        return None


def modulate_response_controls(key: str, wide: bool = False):
    """Shared response controls for every field-modulate flavor:
    min effect, max effect, curve, and steepness.

    ``wide`` switches min/max to free-range number inputs (used by the
    advanced step, where "add"/"set" modes on angle need values beyond
    0..2); the simple mode keeps bounded sliders.
    """
    c1, c2 = st.columns(2)
    if wide:
        lo = c1.number_input("Min effect (field=0)", -10.0, 10.0, 0.15,
                             key=f"{key}_lo",
                             help="Value applied where the field is 0 "
                                  "(weakest).")
        hi = c2.number_input("Max effect (field=1)", -10.0, 10.0, 1.0,
                             key=f"{key}_hi",
                             help="Value applied where the field is 1 "
                                  "(strongest).")
    else:
        lo = c1.slider("Min effect", 0.0, 1.0, 0.15, 0.05,
                       key=f"{key}_lo",
                       help="Scale applied where the field is weakest — "
                            "0 shrinks those cutouts to nothing.")
        hi = c2.slider("Max effect", 0.0, 2.0, 1.0, 0.05,
                       key=f"{key}_hi",
                       help="Scale applied where the field is strongest.")
    c1, c2 = st.columns(2)
    curve = c1.selectbox("Curve",
                         ["linear", "quadratic", "exponential",
                          "gaussian", "sphere"],
                         key=f"{key}_curve",
                         help="Response curve applied to the sampled "
                              "field value before it drives the "
                              "attribute — works with any field source.")
    k = 3.0
    if curve in ("exponential", "gaussian"):
        k = c2.slider("Steepness", 0.5, 10.0, 3.0, 0.5,
                      key=f"{key}_k",
                      help="How sharply the curve bends — higher = a "
                           "more abrupt transition.")
    return lo, hi, curve, k


def rules_controls(alternate: bool, major_every: int, key: str = "rules"):
    if alternate:
        spec_a = shape_spec_controls("Even rows", f"{key}_even",
                                     default_shape="triangle",
                                     default_rot=90.0)
        spec_b = shape_spec_controls("Odd rows", f"{key}_odd",
                                     default_shape="triangle",
                                     default_rot=-90.0)
        return {"even": spec_a, "odd": spec_b}
    if major_every > 0:
        spec_a = shape_spec_controls("Major rows", f"{key}_major",
                                     default_shape="hexagon")
        spec_b = shape_spec_controls("Minor rows", f"{key}_minor",
                                     default_shape="circle")
        return {"major": spec_a, "minor": spec_b}
    spec = shape_spec_controls("All cutouts", f"{key}_all")
    return {"*": spec}


def crop_controls(key: str = "mfg_crop"):
    enabled = st.checkbox("Crop to panel boundary", key=f"{key}_on",
                          help="Clip the pattern to a physical panel "
                               "outline (rectangle or circle).")
    if not enabled:
        return None
    kind = st.selectbox("Boundary", ["Rectangle", "Circle"],
                        key=f"{key}_kind",
                        help="Shape of the panel the pattern is cut "
                             "from.")
    if kind == "Rectangle":
        c1, c2 = st.columns(2)
        w = c1.number_input("Panel width", 10.0, 3000.0, 200.0,
                            key=f"{key}_w",
                            help="Physical panel width.")
        h = c2.number_input("Panel height", 10.0, 3000.0, 150.0,
                            key=f"{key}_h",
                            help="Physical panel height.")
        boundary = Boundary.rect(w, h)
    else:
        d = st.number_input("Panel diameter", 10.0, 3000.0, 120.0,
                            key=f"{key}_d",
                            help="Physical panel diameter.")
        boundary = Boundary.circle(d)
    mode = st.selectbox(
        "Edge handling", ["cull", "slice", "center"], key=f"{key}_mode",
        help="cull: only whole cutouts inside; slice: boolean-cut "
             "straddlers flush with the edge; center: keep if centroid "
             "is inside.")
    margin = st.number_input(
        "Edge padding (solid border)", 0.0, 100.0, 0.0, key=f"{key}_m",
        help="Keep at least this much solid material between every "
             "cutout and the panel edge.")
    outline = st.checkbox("Include panel outline in export", value=True,
                          key=f"{key}_out",
                          help="Add the panel boundary as a cut line in "
                               "the exported DXF/SVG.")
    return Crop(boundary, mode=mode, margin=margin, include_outline=outline)


def manufacturing_controls(key: str = "mfg"):
    crop = crop_controls(key=f"{key}_crop")
    min_hole = st.number_input("Min hole size (0=off)", 0.0, 50.0, 0.0,
                               key=f"{key}_minh",
                               help="Drop cutouts smaller than your "
                                    "process can cut cleanly.")
    min_wall = st.number_input("Min wall thickness (0=off)", 0.0, 50.0, 0.0,
                               key=f"{key}_minw",
                               help="Shrink cutouts as needed so at least "
                                    "this much material remains between "
                                    "neighbors.")
    target_d = st.number_input("Scale to exact size (0=off)",
                               0.0, 3000.0, 0.0, key=f"{key}_fit",
                               help="Uniformly rescale the finished "
                                    "pattern so its largest dimension "
                                    "equals this value.")
    return crop, min_hole, min_wall, target_d


def apply_shape_ops(shapes, crop, min_hole, min_wall, target_d):
    # Size / wall / hole constraints interact, so they are resolved
    # together (see perforata.ops.resolve_constraints) instead of run
    # once sequentially.
    shapes = resolve_constraints(shapes, diameter=target_d,
                                 min_wall=min_wall, min_hole=min_hole)
    if crop is not None:
        shapes = crop.run(shapes)
    return shapes


# ----------------------------------------------------------------------
# Advanced mode: composable modifier stack
# ----------------------------------------------------------------------

STEP_TYPES = ["Rotate", "Scale", "Shear", "Translate", "Polar warp",
              "Field modulate", "Density warp", "Attribute filter",
              "Tag filter"]


def step_controls(idx: int, step_type: str):
    """Render controls for one advanced-mode step; return a modifier
    node (or None if the step is incomplete)."""
    key = f"step{idx}"
    if step_type == "Rotate":
        deg = st.slider("Degrees", -180.0, 180.0, 0.0, 5.0, key=f"{key}_deg",
                        help="Rotate all grid points about the pattern "
                             "center.")
        return Affine.rotation(deg)
    if step_type == "Scale":
        c1, c2 = st.columns(2)
        sx = c1.number_input("Scale X", 0.05, 20.0, 1.0, key=f"{key}_sx",
                             help="Stretch factor along X (1 = "
                                  "unchanged).")
        sy = c2.number_input("Scale Y", 0.05, 20.0, 1.0, key=f"{key}_sy",
                             help="Stretch factor along Y (1 = "
                                  "unchanged).")
        return Affine.scaling(sx, sy)
    if step_type == "Shear":
        c1, c2 = st.columns(2)
        shx = c1.number_input("Shear X", -3.0, 3.0, 0.0, key=f"{key}_shx",
                              help="Slant X by this multiple of Y — rows "
                                   "lean sideways.")
        shy = c2.number_input("Shear Y", -3.0, 3.0, 0.0, key=f"{key}_shy",
                              help="Slant Y by this multiple of X — "
                                   "columns lean up/down.")
        return Affine.shear(shx, shy)
    if step_type == "Translate":
        c1, c2 = st.columns(2)
        dx = c1.number_input("dX", -1000.0, 1000.0, 0.0, key=f"{key}_dx",
                             help="Shift the pattern along X.")
        dy = c2.number_input("dY", -1000.0, 1000.0, 0.0, key=f"{key}_dy",
                             help="Shift the pattern along Y.")
        return Affine.translation(dx, dy)
    if step_type == "Polar warp":
        r_off = st.number_input("Radius offset", 0.0, 1000.0, 100.0,
                                key=f"{key}_roff",
                                help="Bend the grid around a circle of "
                                     "this radius — larger = gentler "
                                     "curvature.")
        return PolarWarp(r_offset=r_off)
    if step_type == "Field modulate":
        field = field_source_controls(key)
        if field is None:
            return None
        attr = st.selectbox("Attribute", ["scale", "angle", "size"],
                            key=f"{key}_attr",
                            help="Point attribute the field drives: scale "
                                 "(relative cutout size), angle (cutout "
                                 "rotation, in turns), or size (absolute "
                                 "cutout size).")
        mode = st.selectbox("Mode", ["multiply", "set", "add"],
                            key=f"{key}_mode",
                            help="How the field value combines with the "
                                 "existing attribute: multiply it, "
                                 "replace it, or add to it.")
        lo, hi, curve, k = modulate_response_controls(key, wide=True)
        return FieldModulate(attr, field, lo=lo, hi=hi, mode=mode,
                             curve=curve, k=k)
    if step_type == "Density warp":
        field = field_source_controls(key)
        if field is None:
            return None
        strength = st.slider("Strength", 0.0, 1.0, 1.0, 0.05,
                             key=f"{key}_str",
                             help="How far points migrate toward "
                                  "field-strong regions (0 = no warp).")
        axes = st.selectbox("Axes", ["both", "x", "y"], key=f"{key}_axes",
                            help="Restrict the migration to one axis or "
                                 "allow both.")
        return DensityWarp(field, strength=strength, axes=axes)
    if step_type == "Attribute filter":
        attr = st.selectbox("Attribute", ["scale", "size"],
                            key=f"{key}_attr",
                            help="Point attribute the keep-range applies "
                                 "to.")
        c1, c2 = st.columns(2)
        lo = c1.number_input("Min", -1000.0, 1000.0, 0.1, key=f"{key}_lo",
                             help="Drop points with a value below this.")
        hi = c2.number_input("Max", -1000.0, 1000.0, 1000.0,
                             key=f"{key}_hi",
                             help="Drop points with a value above this.")
        return AttributeFilter(attr, lo=lo, hi=hi)
    if step_type == "Tag filter":
        tags = st.text_input("Tags to keep (comma-separated)",
                             "default", key=f"{key}_tags",
                             help="Keep only points carrying one of these "
                                  "row/ring tags (e.g. even, odd, major, "
                                  "minor, default).")
        return TagFilter(*[t.strip() for t in tags.split(",") if t.strip()])
    return None


def advanced_stack_controls():
    """Manage the ordered list of steps in session state; render each
    step's controls and return the built modifier list."""
    if "adv_steps" not in st.session_state:
        st.session_state.adv_steps = []

    c1, c2 = st.columns([3, 1])
    new_type = c1.selectbox("Add step", STEP_TYPES,
                            label_visibility="collapsed",
                            help="Modifier type to append to the stack.")
    if c2.button("➕ Add", use_container_width=True,
                 help="Append the selected step to the end of the stack."):
        st.session_state.adv_steps = st.session_state.adv_steps + [new_type]
        st.rerun()

    modifiers = []
    remove = None
    for i, step_type in enumerate(st.session_state.adv_steps):
        with st.expander(f"{i + 1} · {step_type}", expanded=True):
            node = step_controls(i, step_type)
            cc1, cc2, cc3 = st.columns(3)
            if cc1.button("⬆", key=f"up{i}", disabled=(i == 0),
                          help="Move this step earlier in the stack."):
                steps = list(st.session_state.adv_steps)
                steps[i - 1], steps[i] = steps[i], steps[i - 1]
                st.session_state.adv_steps = steps
                st.rerun()
            if cc2.button("⬇", key=f"dn{i}",
                          disabled=(i == len(st.session_state.adv_steps) - 1),
                          help="Move this step later in the stack."):
                steps = list(st.session_state.adv_steps)
                steps[i + 1], steps[i] = steps[i], steps[i + 1]
                st.session_state.adv_steps = steps
                st.rerun()
            if cc3.button("✕", key=f"rm{i}",
                          help="Remove this step from the stack."):
                remove = i
        if node is not None:
            modifiers.append(node)

    if remove is not None:
        steps = list(st.session_state.adv_steps)
        steps.pop(remove)
        st.session_state.adv_steps = steps
        st.rerun()
    return modifiers


# ----------------------------------------------------------------------
# Sidebar: pipeline header + editor
# ----------------------------------------------------------------------

st.title("perforata")
st.caption("Parametric perforation patterns for laser / CNC cutting")

with st.sidebar:
    # --- Pipeline header ------------------------------------------------
    dirty = is_dirty()
    name = st.session_state.get("pipeline_name", "untitled")
    st.markdown(f"### 📐 {name}{' &nbsp;`● modified`' if dirty else ''}")

    with st.expander("Pipeline: load / save / share", expanded=False):
        # Load a factory or user pipeline
        factory_names = [f"[factory] {n}" for n in presets.list_factory()]
        user_names = [f"[mine] {n}" for n in presets.list_presets()]
        pick = st.selectbox("Load pipeline", factory_names + user_names,
                            help="Pick a built-in [factory] or saved "
                                 "[mine] pipeline to load.")
        if st.button("📂 Load", use_container_width=True,
                     help="Load the selected pipeline, replacing the "
                          "current settings."):
            if pick.startswith("[factory] "):
                chosen = pick.removeprefix("[factory] ")
                payload = presets.load_factory(chosen)
            else:
                chosen = pick.removeprefix("[mine] ")
                payload = presets.load(chosen)
            stage_load(payload["ui_state"], chosen)

        st.divider()

        # Save / revert the current pipeline
        save_name = st.text_input("Save as", value=name,
                                  help="Name to save the current pipeline "
                                       "under (stored locally in "
                                       "presets/user/).")
        c1, c2 = st.columns(2)
        if c1.button("💾 Save", use_container_width=True,
                     disabled=not save_name.strip(),
                     help="Save the current settings as a named "
                          "pipeline."):
            snap = snapshot_state()
            presets.save(save_name.strip(), {"ui_state": snap})
            st.session_state["pipeline_name"] = save_name.strip()
            st.session_state["baseline"] = snap
            st.rerun()
        if c2.button("↩ Revert", use_container_width=True,
                     disabled=not dirty,
                     help="Discard edits and restore the pipeline as it "
                          "was loaded."):
            stage_load(st.session_state["baseline"] or {}, name)

        c1, c2 = st.columns(2)
        c1.download_button(
            "🔗 Share .pfp", data=presets.dumps({"ui_state": snapshot_state()}),
            file_name=f"{(save_name.strip() or 'pattern')}.pfp",
            mime="application/octet-stream", use_container_width=True,
            help="Download the current pipeline as a portable .pfp file "
                 "you can share or import elsewhere.")
        if user_names and c2.button("🗑 Delete saved",
                                    use_container_width=True,
                                    help="Delete the saved pipeline "
                                         "selected in 'Load pipeline' "
                                         "above."):
            if pick.startswith("[mine] "):
                presets.delete(pick.removeprefix("[mine] "))
                st.rerun()
            else:
                st.warning("Select one of your saved pipelines to delete.")

        up = st.file_uploader("Import a shared .pfp", type=["pfp"],
                              help="Load a pipeline file someone shared "
                                   "with you.")
        if up is not None and st.button("⤵ Import", use_container_width=True,
                                        help="Load the uploaded .pfp as "
                                             "the current pipeline."):
            try:
                payload = presets.loads(up.read())
                stage_load(payload["ui_state"], up.name.removesuffix(".pfp"))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not load preset: {exc}")

    if st.button("🎬 Demo gallery", use_container_width=True,
                 help="Render every factory preset into one matrix image "
                      "— like the MVP's --demo grid."):
        st.session_state["show_demo"] = True

    st.divider()
    ui_mode = st.radio("Mode", ["Simple", "Advanced"], horizontal=True,
                       key="mode_radio",
                       help="Simple: guided controls (field convolution + "
                            "transform). Advanced: a freely ordered stack "
                            "of modifier steps.")

    # --- Editor ----------------------------------------------------------
    st.header("1 · Grid")
    gen, alternate, major_every = generator_controls()

    st.header("2 · Cutouts")
    rules = rules_controls(alternate, major_every)

    if ui_mode == "Simple":
        st.header("3 · Field convolution")
        modifiers = []
        if st.checkbox("Convolve a field with the grid", key="fconv_on",
                       help="Drive cutout size and/or grid spacing from "
                            "an image, shape gradient, or expression — "
                            "e.g. a circular exponential gradient "
                            "controlling cutout size on a cartesian grid."):
            field = field_source_controls("fconv")
            if field is not None:
                target = st.selectbox(
                    "Apply to",
                    ["Cutout size", "Grid spacing (density)", "Both"],
                    key="fconv_target",
                    help="Cutout size: holes grow/shrink with the field. "
                         "Grid spacing: points migrate so the grid gets "
                         "denser where the field is strong.")
                if target in ("Grid spacing (density)", "Both"):
                    strength = st.slider("Density strength",
                                         0.0, 1.0, 0.8, 0.05,
                                         key="fconv_dstr",
                                         help="How far grid points migrate "
                                              "toward field-strong regions "
                                              "(0 = no change).")
                    modifiers.append(DensityWarp(field, strength=strength,
                                                 region="symmetric"))
                if target in ("Cutout size", "Both"):
                    lo, hi, curve, k = modulate_response_controls("fconv")
                    # All generators are origin-centered; the symmetric
                    # region keeps the field centered on the pattern even
                    # when the cloud's raw bbox is slightly lopsided
                    # (radial grids rarely have a point at exactly 180°).
                    modifiers.append(FieldModulate("scale", field,
                                                   lo=lo, hi=hi,
                                                   curve=curve, k=k,
                                                   region="symmetric"))
                    drop = st.slider("Drop holes scaled below",
                                     0.0, 0.5, 0.1, 0.01, key="fconv_drop",
                                     help="Remove cutouts whose field-"
                                          "driven scale falls below this — "
                                          "cleans up specks in weak-field "
                                          "areas.")
                    if drop > 0:
                        modifiers.append(AttributeFilter("scale", lo=drop))

        st.header("4 · Transform")
        rot_all = st.slider("Rotate pattern (deg)", -180.0, 180.0, 0.0, 5.0,
                            key="xform_rot",
                            help="Rotate the whole pattern about its "
                                 "center.")
        if abs(rot_all) > 1e-9:
            modifiers.insert(0, Affine.rotation(rot_all))
        if st.checkbox("Polar warp (bend grid into a circle)",
                       key="xform_polar",
                       help="Wrap the flat grid around a circle — rows "
                            "become rings."):
            r_offset = st.number_input("Warp radius offset",
                                       0.0, 500.0, 100.0, key="xform_roff",
                                       help="Radius of the circle the grid "
                                            "is bent around — larger = "
                                            "gentler curvature.")
            modifiers.insert(0, PolarWarp(r_offset=r_offset))
    else:
        st.header("3 · Modifier stack")
        st.caption("Steps run top to bottom on the grid points.")
        modifiers = advanced_stack_controls()

    st.header("5 · Manufacturing" if ui_mode == "Simple"
              else "4 · Manufacturing")
    crop, min_hole, min_wall, target_d = manufacturing_controls()

# --- Run the pipeline ---------------------------------------------------
cloud = gen.run()
for mod in modifiers:
    cloud = mod.run(cloud)

shapes = ShapeInstancer(rules=rules).run(cloud)
shapes = apply_shape_ops(shapes, crop, min_hole, min_wall, target_d)

# --- Demo gallery ---------------------------------------------------------
if st.session_state.get("show_demo"):
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        c1.subheader("Demo gallery — every factory preset")
        if c2.button("✕ Close", key="demo_close", use_container_width=True):
            st.session_state["show_demo"] = False
            st.rerun()
        with st.spinner("Rendering all factory presets…"):
            png = demo_gallery_png()
        st.image(png, use_container_width=True)
        st.download_button("⬇ Download gallery PNG", data=png,
                           file_name="perforata-demo-gallery.png",
                           mime="image/png", key="demo_dl",
                           help="Save the preset matrix as a PNG image.")

# --- Preview ------------------------------------------------------------
col_plot, col_info = st.columns([3, 1])
cuts = cutouts_only(shapes)

with col_plot:
    # By default the preview shows cutouts only; the panel outline is
    # still included in the export when requested.
    show_outline = st.checkbox("Show panel outline in preview", value=False,
                               help="Draw the panel boundary in the "
                                    "preview (exports are unaffected).")
    fig = plot_shapes(shapes if show_outline else cuts)
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)

with col_info:
    st.metric("Points", len(cloud))
    st.metric("Cutouts", len(cuts))
    if cuts:
        sizes = [s["size"] for s in cuts]
        st.metric("Smallest hole", f"{min(sizes):.2f}")
        st.metric("Largest hole", f"{max(sizes):.2f}")
        x0, y0, x1, y1 = shapes_bounds(cuts)
        st.metric("Extent", f"{x1 - x0:.1f} × {y1 - y0:.1f}")
        gap = min_spacing(cuts)
        if gap is not None:
            st.metric("Min spacing", f"{gap:.2f}",
                      help="Narrowest wall between any two cutouts "
                           "(bounding-circle estimate: exact for "
                           "circles, slightly conservative for "
                           "polygons).")
        if crop is not None:
            clearance = edge_clearance(cuts, crop.boundary)
            if clearance is not None:
                st.metric("Edge clearance", f"{clearance:.2f}",
                          help="Smallest distance from any cutout to "
                               "the panel edge. Negative = a cutout "
                               "crosses the edge.")

    st.divider()
    st.subheader("Export")
    dxf_bytes = DXFExporter().run(shapes)
    st.download_button("⬇ Download DXF", data=dxf_bytes,
                       file_name="perforata.dxf",
                       mime="application/dxf",
                       use_container_width=True,
                       help="CAD-ready cut file for laser / CNC "
                            "workflows.")
    svg_bytes = SVGExporter().run(shapes)
    st.download_button("⬇ Download SVG", data=svg_bytes,
                       file_name="perforata.svg",
                       mime="image/svg+xml",
                       use_container_width=True,
                       help="Vector file for browsers, Inkscape, or "
                            "Illustrator.")
    config_text = describe_pipeline(gen, modifiers, rules, crop,
                                    min_hole, min_wall, target_d,
                                    cloud=cloud, cuts=cuts)
    st.download_button("⬇ Config dump (.txt)", data=config_text,
                       file_name="perforata-config.txt",
                       mime="text/plain",
                       use_container_width=True,
                       help="Plain-text dump of the widget state and the "
                            "constructed node pipeline, for debugging "
                            "and bug reports.")

    st.divider()
    if st.checkbox("Show grid points (debug)",
                   help="Plot the raw grid points after modifiers, "
                        "before cutouts are instanced."):
        fig2 = plot_pointcloud(cloud)
        st.pyplot(fig2, use_container_width=True)
        plt.close(fig2)

# Capture the full baseline (widget defaults included) right after a
# pipeline load, once every widget has rendered.
if st.session_state.get("baseline") is None:
    st.session_state["baseline"] = snapshot_state()
