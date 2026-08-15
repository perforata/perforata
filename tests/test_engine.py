"""Smoke tests for the perforata node-graph engine."""

import math

import numpy as np
import pytest

from perforata.pointcloud import PointCloud
from perforata.graph import Pipeline, Graph
from perforata.generators import CartesianGrid, HexGrid, ConcentricRings
from perforata.fields import (Constant, LinearGradient, RadialGradient,
                              ShapeGradient, Expression, ImageField,
                              TextField)
from perforata.modifiers import (Affine, FieldModulate, AttributeFilter,
                                 DensityWarp, TagFilter, Merge, PolarWarp)
from perforata.decorators import ShapeInstancer, ShapeSpec, shapes_bounds
from perforata.ops import (Boundary, Crop, MinHoleFilter, FitToSize,
                           MinWall, cutouts_only, min_spacing,
                           edge_clearance)
from perforata.exporters import DXFExporter, SVGExporter


# ----------------------------------------------------------------------
# PointCloud
# ----------------------------------------------------------------------

def test_pointcloud_basics():
    pc = PointCloud(np.array([[0, 0], [1, 1]]), {"size": np.array([1.0, 2.0])})
    assert len(pc) == 2
    assert pc.get("size")[1] == 2.0
    assert pc.get("missing", 5.0)[0] == 5.0
    filtered = pc.filter(pc.x > 0.5)
    assert len(filtered) == 1


def test_pointcloud_concat_fills_missing_attrs():
    a = PointCloud(np.array([[0.0, 0.0]]), {"size": np.array([1.0])})
    b = PointCloud(np.array([[1.0, 1.0]]))
    c = a.concat(b)
    assert len(c) == 2
    assert c.get("size")[1] == 0.0


# ----------------------------------------------------------------------
# Generators
# ----------------------------------------------------------------------

def test_cartesian_grid_covers_extent():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=100, height=50).run()
    assert len(cloud) > 0
    assert cloud.x.max() <= 50 + 1e-6
    assert cloud.y.max() <= 25 + 1e-6
    # Lattice points at multiples of the pitch, centered on the origin:
    # x in {-50..50} = 11 columns, y in {-20..20} = 5 rows
    assert len(cloud) == 11 * 5


def test_cartesian_alternate_tags():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50,
                          alternate=True).run()
    tags = set(cloud.get("tag"))
    assert tags == {"even", "odd"}


def test_cartesian_major_minor():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50,
                          major_every=2).run()
    assert set(cloud.get("tag")) == {"major", "minor"}


def test_cartesian_column_stagger():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50,
                          col_stagger=0.5).run()
    assert len(cloud) > 0
    # Each column shifts vertically by col_stagger * pitch_y: y - 5*col
    # must be a multiple of the pitch.
    cols = cloud.get("col")
    resid = (cloud.y - 5.0 * cols) % 10.0
    resid = np.minimum(resid, 10.0 - resid)
    assert np.all(resid < 1e-6)
    # Adjacent columns must not share y values (they are offset by half
    # a pitch).
    ys0 = set(np.round(cloud.y[cols == 0], 6))
    ys1 = set(np.round(cloud.y[cols == 1], 6))
    assert ys0 and ys1 and not (ys0 & ys1)


def test_cartesian_row_and_column_stagger_combined():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50,
                          stagger=0.25, col_stagger=0.25).run()
    rows, cols = cloud.get("row"), cloud.get("col")
    assert np.allclose(cloud.x, 10.0 * cols + 0.25 * 10.0 * rows)
    assert np.allclose(cloud.y, 10.0 * rows + 0.25 * 10.0 * cols)


def test_cartesian_degenerate_stagger_raises():
    import pytest
    with pytest.raises(ValueError):
        CartesianGrid(stagger=2.0, col_stagger=0.5)


def _edge_margins(cloud, width, height):
    hw, hh = width / 2.0, height / 2.0
    return (hw - cloud.x.max(), cloud.x.min() + hw,
            hh - cloud.y.max(), cloud.y.min() + hh)


def test_cartesian_fit_balances_edge_margins():
    # 250 x 180 at pitch 10: without fit, y-margins differ from x-margins.
    w, h = 250.0, 183.0
    gen = CartesianGrid(pitch_x=10, pitch_y=10, width=w, height=h, fit=True)
    cloud = gen.run()
    right, left, top, bottom = _edge_margins(cloud, w, h)
    # Outermost points sit exactly half the fitted pitch from each edge.
    assert abs(right - left) < 1e-9
    assert abs(top - bottom) < 1e-9
    assert abs(right - gen.pitch_x / 2.0) < 1e-9
    assert abs(top - gen.pitch_y / 2.0) < 1e-9
    # Snapped pitches stay close to the requested ones.
    assert abs(gen.pitch_x - 10.0) < 0.5
    assert abs(gen.pitch_y - 10.0) < 0.5


def test_hex_fit_balances_edge_margins():
    w, h = 250.0, 180.0
    gen = HexGrid(pitch=10, width=w, height=h, fit=True)
    cloud = gen.run()
    right, left, top, bottom = _edge_margins(cloud, w, h)
    assert abs(top - bottom) < 1e-9
    assert abs(top - gen.pitch_y / 2.0) < 1e-9
    # Staggered rows alternate x-extremes, but no point may come closer
    # to a side than half the fitted x-pitch... minus the stagger's half
    # cell — i.e. margins stay within one half-pitch band and symmetric.
    assert min(right, left) > 0
    assert abs(right - left) < gen.pitch_x / 2.0 + 1e-9


def test_hex_grid_neighbor_distance():
    cloud = HexGrid(pitch=10, width=60, height=60).run()
    # Nearest-neighbor distance should equal the pitch
    pts = cloud.xy
    d = np.linalg.norm(pts[None, :, :] - pts[:, None, :], axis=-1)
    d[d < 1e-9] = np.inf
    assert abs(d.min() - 10.0) < 1e-6


def test_concentric_rings_matches_mvp_layout():
    cloud = ConcentricRings(rings=5, r0=10, factor=1.15,
                            mode="exponential").run()
    assert len(cloud) > 0
    radii = np.hypot(cloud.x, cloud.y)
    ring0 = radii[cloud.get("ring") == 0]
    assert np.allclose(ring0, 10.0)
    # angles follow outward normals
    ang = cloud.get("angle")
    assert np.allclose(np.cos(ang) * radii, cloud.x, atol=1e-9)


def test_concentric_rings_invert():
    normal = ConcentricRings(rings=5, r0=10, factor=1.15).run()
    inverted = ConcentricRings(rings=5, r0=10, factor=1.15, invert=True).run()
    # Same overall extent
    assert abs(np.hypot(normal.x, normal.y).max()
               - np.hypot(inverted.x, inverted.y).max()) < 1e-6
    # Inverted: smallest gap (size) on the outermost radius
    r_inv = np.hypot(inverted.x, inverted.y)
    outer = inverted.get("size")[np.argmax(r_inv)]
    inner = inverted.get("size")[np.argmin(r_inv)]
    assert outer < inner


def test_exponential_requires_positive_r0():
    with pytest.raises(ValueError):
        ConcentricRings(rings=3, r0=0.0, mode="exponential").run()


# ----------------------------------------------------------------------
# Fields
# ----------------------------------------------------------------------

def test_field_composition():
    f = 0.5 + 0.5 * Constant(1.0)
    uv = np.array([[0.5, 0.5]])
    assert f.sample(uv)[0] == 1.0


def test_linear_gradient_direction():
    f = LinearGradient(0.0)
    uv = np.array([[0.0, 0.5], [1.0, 0.5]])
    v = f.sample(uv)
    assert v[0] < v[1]


def test_radial_gradient_center():
    f = RadialGradient()
    uv = np.array([[0.5, 0.5], [0.0, 0.0]])
    v = f.sample(uv)
    assert v[0] < 0.01 and v[1] > 0.99


def test_expression_field():
    f = Expression("u * v")
    uv = np.array([[0.5, 0.5]])
    assert abs(f.sample(uv)[0] - 0.25) < 1e-9


def test_shape_gradient_circle_exponential():
    f = ShapeGradient("circle", falloff="exponential", k=3.0)
    uv = np.array([[0.5, 0.5], [0.75, 0.5], [1.0, 0.5]])
    v = f.sample(uv)
    assert v[0] < 1e-9           # zero at center
    assert abs(v[2] - 1.0) < 1e-9  # one at the inscribed boundary
    # exponential curve: value at half distance is below linear (0.5)
    assert v[1] < 0.5


def test_shape_gradient_inverted():
    f = ShapeGradient("circle", invert=True)
    uv = np.array([[0.5, 0.5], [1.0, 0.5]])
    v = f.sample(uv)
    assert v[0] > 0.99 and v[1] < 0.01


def test_text_field_letter_shape():
    """The rendered glyph must be strong inside its strokes and weak in
    the background corners."""
    f = TextField("A")  # glyph=1, background=0
    # Corners of the unit square are background
    corners = np.array([[0.02, 0.02], [0.98, 0.98],
                        [0.02, 0.98], [0.98, 0.02]])
    assert np.all(f.sample(corners) < 0.2)
    # Somewhere inside the glyph must be strong: scan the center column
    # and the crossbar region of "A"
    scan = np.stack([np.full(50, 0.5), np.linspace(0.15, 0.85, 50)], axis=1)
    assert f.sample(scan).max() > 0.8


def test_text_field_invert():
    f = TextField("A", invert=True)  # glyph=0, background=1
    corners = np.array([[0.02, 0.02], [0.98, 0.98]])
    assert np.all(f.sample(corners) > 0.8)


def test_text_field_blur_softens_edges():
    """With blur, values between glyph and background become gradual:
    the field must contain intermediate values near the outline."""
    hard = TextField("O", blur=0.0)
    soft = TextField("O", blur=0.05)
    # Dense scan across the glyph
    g = np.linspace(0.05, 0.95, 60)
    uu, vv = np.meshgrid(g, g)
    uv = np.stack([uu.ravel(), vv.ravel()], axis=1)
    hard_vals = hard.sample(uv)
    soft_vals = soft.sample(uv)
    mid_hard = np.mean((hard_vals > 0.25) & (hard_vals < 0.75))
    mid_soft = np.mean((soft_vals > 0.25) & (soft_vals < 0.75))
    assert mid_soft > 2 * max(mid_hard, 1e-6)  # many more mid-tones
    # Extremes are preserved: still ~1 inside strokes, ~0 in corners
    assert soft.sample(np.array([[0.03, 0.03]]))[0] < 0.2


def test_text_field_empty_text():
    f = TextField("")
    uv = np.array([[0.5, 0.5]])
    assert f.sample(uv)[0] == 0.0


def test_text_field_modulates_grid():
    """End-to-end: a letter drives cutout sizes on a grid."""
    cloud = CartesianGrid(pitch_x=5, pitch_y=5, width=150, height=150).run()
    out = FieldModulate("scale", TextField("O"), lo=0.1, hi=1.0).run(cloud)
    scale = out.get("scale")
    # Cutouts near the ring of the "O" must be larger than at the corners
    assert scale.max() > 3 * scale.min()


def test_shape_gradient_sphere_falloff():
    """Inverted sphere falloff is the height of a hemisphere:
    sqrt(1 - d^2) — 1 at the center, 0 at the inscribed boundary,
    and above linear everywhere between (the ball bulges)."""
    f = ShapeGradient("circle", falloff="sphere", invert=True)
    uv = np.array([[0.5, 0.5], [0.75, 0.5], [0.9, 0.5], [1.0, 0.5]])
    v = f.sample(uv)
    assert abs(v[0] - 1.0) < 1e-9
    assert abs(v[3]) < 1e-9
    # d=0.5 -> sqrt(1 - 0.25) = sqrt(0.75)
    assert abs(v[1] - np.sqrt(0.75)) < 1e-9
    # bulges above the linear (cone) profile
    assert v[1] > 0.5 and v[2] > 0.2


def test_shape_gradient_square_metric():
    f = ShapeGradient("square", falloff="linear")
    # Chebyshev metric: corners of the inscribed square hit 1 together
    uv = np.array([[1.0, 1.0], [1.0, 0.5]])
    v = f.sample(uv)
    assert abs(v[0] - 1.0) < 1e-9 and abs(v[1] - 1.0) < 1e-9


def test_shape_gradient_rectangle_metric():
    # A wide, short rectangle: 1.0 wide, 0.5 tall (centered).
    f = ShapeGradient("rectangle", falloff="linear", width=1.0, height=0.5)
    uv = np.array([
        [0.5, 0.5],    # center -> 0
        [1.0, 0.5],    # right edge of the rect -> 1
        [0.5, 0.75],   # top edge of the rect -> 1
        [0.75, 0.5],   # halfway to the right edge -> 0.5
        [0.5, 1.0],    # beyond the rect vertically -> clamps at 1
    ])
    v = f.sample(uv)
    assert abs(v[0]) < 1e-9
    assert abs(v[1] - 1.0) < 1e-9
    assert abs(v[2] - 1.0) < 1e-9
    assert abs(v[3] - 0.5) < 1e-9
    assert abs(v[4] - 1.0) < 1e-9


def test_shape_gradient_rectangle_default_is_square():
    # Default width=height=1.0 matches the square metric.
    rect = ShapeGradient("rectangle", falloff="linear")
    square = ShapeGradient("square", falloff="linear")
    uv = np.random.default_rng(0).random((50, 2))
    assert np.allclose(rect.sample(uv), square.sample(uv))


def test_image_field_from_array():
    img = np.zeros((10, 10))
    img[:, 5:] = 1.0  # right half white
    f = ImageField(img, fit="stretch")
    uv = np.array([[0.1, 0.5], [0.9, 0.5]])
    v = f.sample(uv)
    assert v[0] < 0.2 and v[1] > 0.8


def test_image_field_vertical_orientation():
    img = np.zeros((10, 10))
    img[0, :] = 1.0  # top row white
    f = ImageField(img, fit="stretch")
    v = f.sample(np.array([[0.5, 0.99], [0.5, 0.01]]))
    assert v[0] > 0.8  # v=1 (top of pattern) samples image row 0
    assert v[1] < 0.2


# ----------------------------------------------------------------------
# Modifiers
# ----------------------------------------------------------------------

def test_affine_rotation_updates_positions_and_angles():
    cloud = PointCloud(np.array([[1.0, 0.0]]),
                       {"angle": np.array([0.0]), "size": np.array([2.0])})
    out = Affine.rotation(90).run(cloud)
    assert np.allclose(out.xy, [[0.0, 1.0]], atol=1e-9)
    assert abs(out.get("angle")[0] - math.pi / 2) < 1e-9
    assert out.get("size")[0] == 2.0  # rotation preserves size


def test_affine_scaling_updates_size():
    cloud = PointCloud(np.array([[1.0, 1.0]]), {"size": np.array([2.0])})
    out = Affine.scaling(2.0).run(cloud)
    assert np.allclose(out.xy, [[2.0, 2.0]])
    assert out.get("size")[0] == 4.0


def test_affine_composition():
    t = Affine.rotation(90).then(Affine.translation(1.0, 0.0))
    cloud = PointCloud(np.array([[1.0, 0.0]]))
    out = t.run(cloud)
    assert np.allclose(out.xy, [[1.0, 1.0]], atol=1e-9)


def test_field_modulate_scale():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=100, height=100).run()
    field = LinearGradient(0.0)
    out = FieldModulate("scale", field, lo=0.0, hi=1.0).run(cloud)
    scale = out.get("scale")
    # points on the left should be smaller than on the right
    left = scale[np.argmin(out.x)]
    right = scale[np.argmax(out.x)]
    assert left < right


def test_field_modulate_symmetric_region_centers_on_origin():
    """A lopsided cloud (no point at exactly the -x extreme, like a
    radial grid whose angles skip 180°) must not shift the field center
    away from the origin when region='symmetric'."""
    # Asymmetric bbox: x in [-8, 10], y in [-10, 10]
    pts = np.array([[-8.0, 0.0], [10.0, 0.0], [0.0, -10.0],
                    [0.0, 10.0], [0.0, 0.0]])
    cloud = PointCloud(pts, {"size": np.full(5, 1.0)})
    field = RadialGradient(invert=True)  # strong at field center

    sym = FieldModulate("scale", field, lo=0.0, hi=1.0,
                        region="symmetric").run(cloud)
    scale = sym.get("scale")
    # The origin point must get the peak value...
    assert scale[4] == max(scale)
    # ...and symmetric points (±10 on y) must get equal values.
    assert abs(scale[2] - scale[3]) < 1e-9

    # Default bbox region shifts the field center off the origin —
    # the origin point no longer receives the field's peak value.
    bbox = FieldModulate("scale", field, lo=0.0, hi=1.0).run(cloud)
    assert bbox.get("scale")[4] < scale[4] - 1e-6


def test_attribute_filter():
    cloud = PointCloud(np.array([[0.0, 0.0], [1.0, 1.0]]),
                       {"scale": np.array([0.05, 0.9])})
    out = AttributeFilter("scale", lo=0.1).run(cloud)
    assert len(out) == 1


def test_tag_filter_and_merge():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50,
                          alternate=True).run()
    even = TagFilter("even").run(cloud)
    odd = TagFilter("odd").run(cloud)
    merged = Merge().run(even, odd)
    assert len(even) + len(odd) == len(cloud) == len(merged)


def test_polar_warp():
    cloud = PointCloud(np.array([[0.0, 10.0]]), {"angle": np.array([0.0])})
    out = PolarWarp().run(cloud)
    assert np.allclose(out.xy, [[10.0, 0.0]], atol=1e-9)


def test_density_warp_compresses_toward_field():
    """A gradient field convolved with grid spacing should pull points
    toward the strong side, shrinking the local pitch there."""
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=100, height=100).run()
    field = LinearGradient(0.0)  # strong on the right
    out = DensityWarp(field, strength=1.0, axes="x").run(cloud)
    # Bounding box preserved
    assert abs(out.x.min() - cloud.x.min()) < 1e-6
    assert abs(out.x.max() - cloud.x.max()) < 1e-6
    # Mean shifts right (points migrate toward high density)
    assert out.x.mean() > cloud.x.mean() + 1.0
    # Local size attribute shrinks on the dense (right) side
    sizes_sorted_by_x = out.get("size")[np.argsort(out.x)]
    assert sizes_sorted_by_x[-2] < sizes_sorted_by_x[1]


def test_density_warp_zero_strength_noop():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50).run()
    out = DensityWarp(LinearGradient(0.0), strength=0.0).run(cloud)
    assert np.allclose(out.xy, cloud.xy)


# ----------------------------------------------------------------------
# Decorators
# ----------------------------------------------------------------------

def test_instancer_tag_rules():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=50, height=50,
                          alternate=True).run()
    shapes = ShapeInstancer(rules={
        "even": ShapeSpec("triangle", fill=0.8, rotation=90),
        "odd": ShapeSpec("triangle", fill=0.8, rotation=-90),
    }).run(cloud)
    assert len(shapes) == len(cloud)
    assert all(s["type"] == "polygon" and len(s["points"]) == 3
               for s in shapes)


def test_instancer_fallback_rule():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=30, height=30).run()
    shapes = ShapeInstancer(shape="circle", fill=0.5).run(cloud)
    assert len(shapes) == len(cloud)
    assert shapes[0]["type"] == "circle"
    # circle radius = 0.5 * pitch * fill = 2.5
    assert abs(shapes[0]["radius"] - 2.5) < 1e-9


def test_instancer_respects_scale_attribute():
    cloud = PointCloud(np.array([[0.0, 0.0]]),
                       {"size": np.array([10.0]), "scale": np.array([0.5])})
    shapes = ShapeInstancer(shape="circle", fill=1.0).run(cloud)
    assert abs(shapes[0]["radius"] - 2.5) < 1e-9


def test_instancer_fill_is_inscribed_diameter():
    """fill = 1.0 means the narrowest opening equals the pitch, for any
    shape — so neighbors just touch instead of leaving shape-dependent
    gaps (hexagons used to come out ~13% smaller than circles)."""
    cloud = PointCloud(np.array([[0.0, 0.0]]), {"size": np.array([10.0])})
    for shape in ("hexagon", "square", "triangle"):
        shapes = ShapeInstancer(shape=shape, fill=1.0).run(cloud)
        assert abs(shapes[0]["size"] - 10.0) < 1e-9, shape
    shapes = ShapeInstancer(shape="circle", fill=1.0).run(cloud)
    assert abs(shapes[0]["size"] - 10.0) < 1e-9


# ----------------------------------------------------------------------
# Ops
# ----------------------------------------------------------------------

def _circle_shapes():
    cloud = CartesianGrid(pitch_x=10, pitch_y=10, width=100, height=100).run()
    return ShapeInstancer(shape="circle", fill=0.5).run(cloud)


def test_crop_cull():
    shapes = _circle_shapes()
    cropped = Crop(Boundary.circle(60), mode="cull").run(shapes)
    assert 0 < len(cropped) < len(shapes)
    for s in cropped:
        cx, cy = s["center"]
        assert math.hypot(cx, cy) + s["radius"] <= 30 + 1e-6


def test_crop_slice_produces_flush_edge():
    shapes = _circle_shapes()
    cropped = Crop(Boundary.rect(55, 55), mode="slice").run(shapes)
    x0, y0, x1, y1 = shapes_bounds(cropped)
    assert x0 >= -27.5 - 1e-6 and x1 <= 27.5 + 1e-6


def test_crop_outline_included():
    shapes = _circle_shapes()
    cropped = Crop(Boundary.circle(60), mode="cull",
                   include_outline=True).run(shapes)
    assert cropped[-1]["type"] == "circle"
    assert cropped[-1]["radius"] == 30


def test_min_hole_filter():
    shapes = [{"type": "circle", "center": (0, 0), "radius": 1, "size": 2},
              {"type": "circle", "center": (5, 0), "radius": 2, "size": 4}]
    out = MinHoleFilter(3.0).run(shapes)
    assert len(out) == 1


def test_fit_to_size():
    shapes = _circle_shapes()
    out = FitToSize(diameter=120.0).run(shapes)
    x0, y0, x1, y1 = shapes_bounds(out)
    assert abs(max(x1 - x0, y1 - y0) - 120.0) < 1e-6


def test_outline_is_tagged_and_filterable():
    shapes = _circle_shapes()
    cropped = Crop(Boundary.circle(60), mode="cull",
                   include_outline=True).run(shapes)
    assert cropped[-1].get("outline") is True
    cuts = cutouts_only(cropped)
    assert len(cuts) == len(cropped) - 1
    assert all(not s.get("outline") for s in cuts)


def test_min_spacing():
    shapes = [{"type": "circle", "center": (0, 0), "radius": 2, "size": 4},
              {"type": "circle", "center": (10, 0), "radius": 3, "size": 6},
              {"type": "circle", "center": (30, 0), "radius": 1, "size": 2}]
    gap = min_spacing(shapes)
    assert abs(gap - 5.0) < 1e-9  # 10 - 2 - 3


def test_min_spacing_ignores_outline():
    shapes = [{"type": "circle", "center": (0, 0), "radius": 2, "size": 4},
              {"type": "circle", "center": (10, 0), "radius": 2, "size": 4},
              {"type": "circle", "center": (0, 0), "radius": 5, "size": 10,
               "outline": True}]
    gap = min_spacing(shapes)
    assert abs(gap - 6.0) < 1e-9  # outline (touching both) excluded


def test_min_spacing_single_shape_none():
    assert min_spacing([{"type": "circle", "center": (0, 0),
                         "radius": 1, "size": 2}]) is None


def test_edge_clearance_inside():
    b = Boundary.circle(100)  # radius 50
    shapes = [{"type": "circle", "center": (0, 0), "radius": 10, "size": 20},
              {"type": "circle", "center": (30, 0), "radius": 5, "size": 10}]
    d = edge_clearance(shapes, b)
    assert abs(d - 15.0) < 0.05  # 50 - 30 - 5, within buffer tolerance


def test_edge_clearance_crossing_is_negative():
    b = Boundary.circle(100)
    shapes = [{"type": "circle", "center": (48, 0), "radius": 5, "size": 10}]
    d = edge_clearance(shapes, b)
    assert d < 0  # protrudes ~3 past the edge
    assert abs(d + 3.0) < 0.1


def test_crop_margin_respects_padding():
    """Edge padding must leave at least that much clearance."""
    shapes = _circle_shapes()
    pad = 8.0
    cropped = Crop(Boundary.circle(90), mode="cull", margin=pad).run(shapes)
    d = edge_clearance(cropped, Boundary.circle(90))
    assert d is not None and d >= pad - 0.05


def test_min_wall_shrinks():
    shapes = [{"type": "circle", "center": (0, 0), "radius": 4.5, "size": 9},
              {"type": "circle", "center": (10, 0), "radius": 4.5, "size": 9}]
    out = MinWall(2.0).run(shapes)
    gap = 10 - out[0]["radius"] - out[1]["radius"]
    assert gap >= 2.0 - 1e-6


# ----------------------------------------------------------------------
# Exporters
# ----------------------------------------------------------------------

def test_dxf_exporter_bytes():
    shapes = _circle_shapes()[:5]
    data = DXFExporter().run(shapes)
    assert isinstance(data, bytes)
    assert b"CIRCLE" in data


def test_svg_exporter_bytes():
    shapes = _circle_shapes()[:5]
    data = SVGExporter().run(shapes)
    assert data.startswith(b"<svg")
    assert b"circle" in data


def test_dxf_exporter_file(tmp_path):
    shapes = _circle_shapes()[:5]
    path = str(tmp_path / "out.dxf")
    result = DXFExporter(path=path).run(shapes)
    assert result == path
    import ezdxf
    doc = ezdxf.readfile(path)
    assert len(list(doc.modelspace())) == 5


# ----------------------------------------------------------------------
# Graph / Pipeline composition
# ----------------------------------------------------------------------

def test_pipeline_end_to_end():
    pipe = Pipeline(
        HexGrid(pitch=10, width=100, height=100),
        ShapeInstancer(shape="hexagon", fill=0.7),
        MinHoleFilter(1.0),
    )
    shapes = pipe.run()
    assert len(shapes) > 0


def test_graph_shared_upstream():
    g = Graph()
    g.add("grid", CartesianGrid(pitch_x=10, pitch_y=10,
                                width=50, height=50, alternate=True))
    g.add("even", TagFilter("even"), "grid")
    g.add("odd", TagFilter("odd"), "grid")
    g.add("merged", Merge(), "even", "odd")
    g.add("cuts", ShapeInstancer(shape="circle", fill=0.5), "merged")
    shapes = g.run("cuts")
    grid = g.run("grid")
    assert len(shapes) == len(grid)


def test_graph_cycle_detection():
    g = Graph()
    g.add("a", Merge())
    with pytest.raises(ValueError):
        g.add("b", Merge(), "missing")


# ----------------------------------------------------------------------
# Presets
# ----------------------------------------------------------------------

def test_preset_roundtrip(tmp_path):
    """Presets store JSON-safe UI state; a saved preset must load back
    verbatim and rebuild an identical pipeline via the demo interpreter."""
    from perforata import presets
    from perforata.demo import run_state
    payload = presets.load_factory("spiral-galaxy")
    presets.save("test-pipe", payload, directory=tmp_path)
    assert presets.list_presets(directory=tmp_path) == ["test-pipe"]

    loaded = presets.load("test-pipe", directory=tmp_path)
    assert loaded == payload
    assert len(run_state(loaded["ui_state"])) > 0

    assert presets.delete("test-pipe", directory=tmp_path)
    assert presets.list_presets(directory=tmp_path) == []


def test_preset_bytes_roundtrip():
    from perforata import presets
    payload = {"ui_state": {"gen_type": "Hexagonal", "gen_p": 10.0}}
    data = presets.dumps(payload)
    assert presets.loads(data) == payload


def test_preset_rejects_garbage():
    from perforata import presets
    with pytest.raises(ValueError):
        presets.loads(b'{"not": "a preset"}')
    import pickle
    with pytest.raises(ValueError, match="no longer supported"):
        presets.loads(pickle.dumps({"not": "a preset"}))


def test_factory_presets_are_valid_ui_state():
    """Factory presets are stored as UI widget state so they hydrate the
    exact same controls a user would set by hand. Validate structure and
    the values of the structural widgets the app keys off."""
    from perforata import presets
    names = presets.list_factory()
    assert len(names) >= 4
    for name in names:
        p = presets.load_factory(name)
        ui = p["ui_state"]
        assert ui["mode_radio"] in ("Simple", "Advanced"), name
        assert ui["gen_type"] in ("Cartesian", "Hexagonal",
                                  "Concentric rings"), name
        assert ui["gen_tagmode"] in ("Uniform", "Alternate rows (even/odd)",
                                     "Major every Nth row"), name
        # Number-input values must be floats (int only where the widget
        # is an int input: gen_r, gen_sp, gen_majn) or Streamlit raises.
        int_keys = {"gen_r", "gen_sp", "gen_majn"}
        for k, v in ui.items():
            if k in int_keys:
                assert isinstance(v, int), f"{name}: {k} must be int"
            elif isinstance(v, bool) or isinstance(v, str):
                continue
            elif isinstance(v, (int, float)):
                assert isinstance(v, float), f"{name}: {k} must be float"


def test_factory_fan_grill_geometry_survives_constraints():
    """The 120mm fan preset's geometry must not be decimated by its own
    min-hole/min-wall constraints (regression: frozen/empty preview)."""
    from perforata import presets
    from perforata.ops import resolve_constraints
    ui = presets.load_factory("classic-fan-grill-120mm")["ui_state"]
    gen = ConcentricRings(rings=ui["gen_r"], r0=ui["gen_r0"],
                          factor=ui["gen_f"], mode=ui["gen_mode"])
    cloud = gen.run()
    shapes = ShapeInstancer(
        shape=ui["rules_all_shape"], fill=ui["rules_all_fill"]).run(cloud)
    n_raw = len(shapes)
    shapes = resolve_constraints(shapes, diameter=ui["mfg_fit"],
                                 min_wall=ui["mfg_minw"],
                                 min_hole=ui["mfg_minh"])
    assert len(shapes) == n_raw  # nothing dropped
    from perforata.decorators import shapes_bounds as _sb
    x0, y0, x1, y1 = _sb(shapes)
    assert abs(max(x1 - x0, y1 - y0) - 120.0) < 0.5
    assert all(s["size"] >= 3.0 - 1e-6 for s in shapes)


def test_image_convolution_end_to_end():
    """The headline feature: a logo image modulates cutout sizes."""
    img = np.ones((20, 20))
    img[5:15, 5:15] = 0.0  # dark square in the middle
    cloud = CartesianGrid(pitch_x=5, pitch_y=5, width=100, height=100).run()
    cloud = FieldModulate("scale",
                          ImageField(img, invert=True, fit="stretch"),
                          lo=0.1, hi=1.0).run(cloud)
    shapes = ShapeInstancer(shape="circle", fill=0.6).run(cloud)
    center = [s for s in shapes
              if abs(s["center"][0]) < 20 and abs(s["center"][1]) < 20]
    edge = [s for s in shapes
            if abs(s["center"][0]) > 40 or abs(s["center"][1]) > 40]
    avg_c = np.mean([s["radius"] for s in center])
    avg_e = np.mean([s["radius"] for s in edge])
    assert avg_c > 2 * avg_e  # holes big where the image is dark
