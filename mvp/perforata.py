# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "ezdxf",
#     "matplotlib",
# ]
# ///

import ezdxf
import math
import argparse

def get_radius(n, r0, factor, mode):
    """Define the radius of the n-th ring."""
    if mode == "exponential":
        if r0 <= 0:
            raise ValueError(
                "exponential mode requires --r0 > 0: with r0 <= 0 every ring "
                "radius collapses to the same value and no cutouts can be placed"
            )
        # Distance grows by a percentage each ring (e.g., 1.15 = 15% growth)
        return r0 * (factor ** n)
    elif mode == "quadratic":
        # Distance grows quadratically (factor scales the x^2 curve)
        return r0 + factor * (n ** 2)
    elif mode == "linear":
        # Uniformly spaced rings
        return r0 + factor * n
    else:
        raise ValueError(f"Unknown mode {mode}")

def make_ring_polygon(ring_shape, R, rect_aspect, ring_rot=0.0):
    """
    Build the CCW vertex list for a non-circular ring at 'inradius' R
    (distance from center to the nearest edge).

    ring_shape: "square", "hexagon", or "rect"
    rect_aspect: (half_w, half_h) normalized so min(half_w, half_h) == 1;
                 only used for "rect".
    ring_rot: rotation of the ring polygon itself, in radians.
    """
    if ring_shape == "rect":
        hw, hh = rect_aspect
        verts = [(hw * R, hh * R), (-hw * R, hh * R),
                 (-hw * R, -hh * R), (hw * R, -hh * R)]
    else:
        n = 4 if ring_shape == "square" else 6
        # Circumradius for a regular n-gon with inradius R
        Rc = R / math.cos(math.pi / n)
        verts = []
        for k in range(n):
            # Offset by pi/n so an edge midpoint sits at angle 0 (flat side right)
            a = 2 * math.pi * k / n + math.pi / n
            verts.append((Rc * math.cos(a), Rc * math.sin(a)))

    if ring_rot:
        c, s = math.cos(ring_rot), math.sin(ring_rot)
        verts = [(x * c - y * s, x * s + y * c) for x, y in verts]
    return verts

def polygon_perimeter(verts):
    """Total perimeter and per-edge lengths of a closed polygon."""
    n = len(verts)
    lengths = []
    for k in range(n):
        x0, y0 = verts[k]
        x1, y1 = verts[(k + 1) % n]
        lengths.append(math.hypot(x1 - x0, y1 - y0))
    return sum(lengths), lengths

def point_on_polygon(verts, lengths, perimeter, s):
    """
    Point at arc length s along a closed CCW polygon, plus the outward
    normal angle at that point (used to orient cutouts).
    """
    s = s % perimeter
    n = len(verts)
    for k in range(n):
        if s <= lengths[k] or k == n - 1:
            x0, y0 = verts[k]
            x1, y1 = verts[(k + 1) % n]
            L = lengths[k]
            t = s / L if L > 0 else 0.0
            px = x0 + (x1 - x0) * t
            py = y0 + (y1 - y0) * t
            # Tangent along travel direction; outward normal for a CCW
            # polygon is the tangent rotated -90 degrees.
            tx, ty = (x1 - x0) / L, (y1 - y0) / L
            normal_angle = math.atan2(-tx, ty)
            return px, py, normal_angle
        s -= lengths[k]
    # Unreachable, but keep type checkers happy
    return verts[0][0], verts[0][1], 0.0

def ring_item_count(ring_shape, R, spacing, spokes, rect_aspect, ring_rot=0.0):
    """Number of cutouts placed on one ring: forced by spokes if > 0,
    otherwise the ring perimeter divided by the local spacing."""
    if spokes > 0:
        return spokes
    if ring_shape == "circle":
        return max(1, int(round(2 * math.pi * R / spacing)))
    verts = make_ring_polygon(ring_shape, R, rect_aspect, ring_rot)
    perimeter, _ = polygon_perimeter(verts)
    return max(1, int(round(perimeter / spacing)))

def ring_positions(ring_shape, R, spacing, num_items, phase,
                   rect_aspect, corners=False, ring_rot=0.0):
    """
    Compute cutout centers and orientations for one ring.

    Returns a list of (cx, cy, orientation_angle).

    num_items: number of cutouts to place (see ring_item_count).
    phase: angular offset as a fraction of a full lap. The caller combines
           twist and cumulative stagger into this single value.
    spacing: local radial gap; only used to fill polygon sides in corners mode.
    corners: for polygonal rings, anchor a cutout exactly on each corner and
             distribute the remainder evenly along each side. Twist, stagger
             and spokes are ignored in this mode since positions are anchored.
    ring_rot: rotation of the ring shape itself, in radians. For circular
              rings this is a phase offset of the starting angle.
    """
    if ring_shape == "circle":
        out = []
        d_theta = 2 * math.pi / num_items
        for j in range(num_items):
            theta = j * d_theta + phase * 2 * math.pi + ring_rot
            out.append((R * math.cos(theta), R * math.sin(theta), theta))
        return out

    verts = make_ring_polygon(ring_shape, R, rect_aspect, ring_rot)

    if corners:
        # Anchor mode: one cutout per corner, rest distributed along sides
        out = []
        n = len(verts)
        for k in range(n):
            x0, y0 = verts[k]
            x1, y1 = verts[(k + 1) % n]
            # Corner cutout, oriented along the outward radial direction
            # (the corner bisector for shapes centered on the origin)
            out.append((x0, y0, math.atan2(y0, x0)))
            # Fill the side between this corner and the next
            L = math.hypot(x1 - x0, y1 - y0)
            if L <= 0:
                continue
            tx, ty = (x1 - x0) / L, (y1 - y0) / L
            normal_angle = math.atan2(-tx, ty)
            n_mid = max(0, int(round(L / spacing)) - 1)
            for m in range(1, n_mid + 1):
                t = m / (n_mid + 1)
                out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, normal_angle))
        return out

    # Polygonal rings: distribute by arc length along the perimeter
    perimeter, lengths = polygon_perimeter(verts)
    out = []
    for j in range(num_items):
        s = ((j / num_items) + phase) * perimeter
        px, py, normal_angle = point_on_polygon(verts, lengths, perimeter, s)
        out.append((px, py, normal_angle))
    return out

def make_cutout(shape_type, cx, cy, shape_r, ori):
    """
    Build one cutout shape dict.

    shape_r is the circumradius; 'size' is the inscribed diameter (narrowest
    opening), which is what matters for airflow / minimum feature checks.
    """
    if shape_type == "circle":
        return {"type": "circle", "center": (cx, cy),
                "radius": shape_r, "size": 2.0 * shape_r}

    num_sides = 4 if shape_type == "square" else 6
    # Offset so a flat edge faces the ring path nicely
    offset = math.pi / num_sides if shape_type == "square" else 0
    points = []
    for k in range(num_sides):
        angle = ori + offset + k * (2 * math.pi / num_sides)
        points.append((cx + shape_r * math.cos(angle),
                       cy + shape_r * math.sin(angle)))
    inscribed = 2.0 * shape_r * math.cos(math.pi / num_sides)
    return {"type": "polygon", "points": points, "size": inscribed}

def _shape_bounds(shape):
    """Return (min_x, min_y, max_x, max_y) for a shape dict."""
    if shape["type"] == "circle":
        cx, cy = shape["center"]
        r = shape["radius"]
        return cx - r, cy - r, cx + r, cy + r
    xs = [p[0] for p in shape["points"]]
    ys = [p[1] for p in shape["points"]]
    return min(xs), min(ys), max(xs), max(ys)

def _scale_shape(shape, scale):
    """Uniformly scale a shape dict about the origin, in place."""
    if shape["type"] == "circle":
        cx, cy = shape["center"]
        shape["center"] = (cx * scale, cy * scale)
        shape["radius"] *= scale
    else:
        shape["points"] = [(px * scale, py * scale) for px, py in shape["points"]]
    shape["size"] *= scale

def _target_scale(shapes, ring_shape, rect_dims, diameter):
    """Scale factor needed to fit `shapes` to the requested target size
    (1.0 if no explicit size was requested or shapes are empty)."""
    if not shapes:
        return 1.0
    if ring_shape == "rect" and rect_dims:
        min_x = min(_shape_bounds(s)[0] for s in shapes)
        min_y = min(_shape_bounds(s)[1] for s in shapes)
        max_x = max(_shape_bounds(s)[2] for s in shapes)
        max_y = max(_shape_bounds(s)[3] for s in shapes)
        bbox_w, bbox_h = max_x - min_x, max_y - min_y
        if bbox_w > 0 and bbox_h > 0:
            return min(rect_dims[0] / bbox_w, rect_dims[1] / bbox_h)
        return 1.0
    if diameter is not None:
        max_extent = 0.0
        for s in shapes:
            if s["type"] == "circle":
                cx, cy = s["center"]
                max_extent = max(max_extent, math.hypot(cx, cy) + s["radius"])
            else:
                for px, py in s["points"]:
                    max_extent = max(max_extent, math.hypot(px, py))
        if max_extent > 0:
            return (diameter / 2.0) / max_extent
    return 1.0

def _shape_center(shape):
    """Centroid of a shape dict."""
    if shape["type"] == "circle":
        return shape["center"]
    pts = shape["points"]
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))

def _outer_radius(shape):
    """Bounding-circle radius of a shape about its centroid.

    Exact for circles; for polygons this is the circumradius, making
    gap estimates slightly conservative (safe for wall thickness)."""
    if shape["type"] == "circle":
        return shape["radius"]
    cx, cy = _shape_center(shape)
    return max(math.hypot(px - cx, py - cy) for px, py in shape["points"])

def _min_gap(shapes):
    """Find the narrowest wall between any two neighboring cutouts.

    Uses bounding-circle distance (gap = center distance - r1 - r2), which
    is exact for circles and slightly conservative for polygons. A spatial
    hash keeps this fast for large patterns.

    Returns (gap, i, j) for the closest pair, or None if fewer than 2 shapes.
    """
    n = len(shapes)
    if n < 2:
        return None
    centers = [_shape_center(s) for s in shapes]
    radii = [_outer_radius(s) for s in shapes]
    # Cell sized to catch any pair whose gap could plausibly be "small"
    cell = max(4.0 * max(radii), 1e-9)
    grid = {}
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

def compute_shapes(shape_type, num_rings, r0, factor, mode, fill_factor, invert,
                   twist=0.0, spokes=0, stagger=0.0,
                   ring_shape="circle", rect_dims=None,
                   diameter=None, min_hole=0.0, center_hole=0.0,
                   center_shape="circle", rotation=0.0, rotation_mode="normal",
                   corners=False, ring_rotation=0.0, center_rotation=0.0,
                   min_wall=0.0):
    """
    Compute all cutout geometry.

    twist: angular offset (in degrees) applied cumulatively per ring, creating
    a spiral/twist effect between successive rings.

    spokes: if > 0, force this exact number of shapes on every ring so they
    align into radial spokes. Combined with twist, this produces visible
    spiral arms. If 0, the count per ring is computed from the local spacing.

    stagger: fraction of each ring's own angular pitch (spacing between
    neighboring shapes) to offset per ring, applied cumulatively. E.g. 0.5
    staggers rings like brickwork. Unlike twist (absolute degrees), stagger
    stays visible in auto-density mode because it is relative to each ring's
    pitch and thus immune to rotational aliasing.

    ring_shape: "circle", "square", "hexagon", or "rect". Non-circular rings
    are concentric polygons; cutouts are distributed evenly along the
    perimeter and oriented to the local outward normal.

    rect_dims: (width, height) of the bounding rectangle for ring_shape="rect".
    The whole pattern (including cut extents) is uniformly scaled to fit
    inside this rectangle.

    diameter: if given, the whole pattern (including cut extents) is
    uniformly scaled so its overall size exactly equals this value. This is
    the explicit physical size control, e.g. match your fan's grill opening.
    (Ignored for ring_shape="rect"; use rect_dims there instead.)

    min_hole: minimum allowed hole size in final (scaled) units, measured as
    the inscribed diameter (the narrowest opening) of each cutout. Holes
    smaller than this are dropped — useful to respect your cutter's kerf or
    minimum feature size.

    min_wall: minimum material wall thickness between neighboring cutouts,
    in final (scaled) units. If the narrowest wall anywhere in the finished
    pattern is thinner than this, the fill factor is reduced globally and
    the whole pattern is rebuilt until the constraint is met. This keeps the
    proportional aesthetic intact (all holes shrink together) while
    guaranteeing no flimsy webbing that would break during cutting.

    center_hole: if > 0, add a cutout of this inscribed diameter at the
    center (inside the innermost ring), e.g. a fan hub clearance hole.
    Specified in final (scaled) units.

    center_shape: shape of the center cutout: "circle", "square", or "hexagon".

    rotation: extra rotation (degrees) applied to every cutout shape.

    rotation_mode: "normal" (default) — rotation is added on top of each
    cutout's local outward-normal orientation; "global" — all cutouts share
    the same absolute orientation of `rotation` degrees, ignoring the normal.

    ring_rotation: rotation (degrees) of the ring shape itself — rotates the
    concentric polygons (or the start angle on circular rings) without
    affecting cutout self-orientation, which stays tied to the (rotated)
    local normals. Independent from `rotation` and `center_rotation`.

    center_rotation: rotation (degrees) of the center cutout, independent of
    the ring and cutout rotations.

    corners: for non-circular rings, force cutouts exactly on ring corners
    and distribute the rest evenly along the sides. Ignores twist/stagger/
    spokes on those rings.

    Returns a list of shape dicts (all sizes in final units):
      - {"type": "circle", "center": (x, y), "radius": r, "size": inscribed_dia}
      - {"type": "polygon", "points": [(x, y), ...], "size": inscribed_dia}
    """
    # Normalized aspect for rect rings: min half-extent == 1
    rect_aspect = (1.0, 1.0)
    if ring_shape == "rect":
        if not rect_dims:
            raise ValueError("ring_shape='rect' requires rect_dims=(width, height)")
        w, h = rect_dims
        m = min(w, h)
        rect_aspect = (w / m, h / m)

    def _generate(fill):
        """Build the raw (unscaled) cutout list for a given fill factor."""
        out = []
        rot_rad = math.radians(rotation)
        ring_rot_rad = math.radians(ring_rotation)
        # Stagger accumulates ring by ring as a fraction of *that ring's own*
        # pitch. It must be summed incrementally: a closed-form
        # `stagger * i / num_items` aliases to a constant offset whenever
        # num_items grows with the ring (linear/quadratic auto-density),
        # which visually cancels the stagger on outer rings.
        cumulative_stagger = 0.0
        for i in range(num_rings):
            # Apply inversion if requested
            effective_i = (num_rings - 1 - i) if invert else i

            R = get_radius(effective_i, r0, factor, mode)

            # Calculate local radial spacing (distance to the adjacent ring)
            if effective_i == 0:
                dR = get_radius(1, r0, factor, mode) - R
            else:
                dR = R - get_radius(effective_i - 1, r0, factor, mode)

            # Map this radius R to its physical position on the grid.
            # If inverted, the *math* says this ring is small/dense, but we
            # want it physically mapped to the outer edge of the pattern.
            # Note: this reflection preserves adjacent-ring gaps, so dR
            # (computed from the mathematical sequence) still equals the
            # physical distance to the neighboring ring after inversion.
            if invert:
                max_R = get_radius(num_rings - 1, r0, factor, mode)
                min_R = get_radius(0, r0, factor, mode)
                physical_R = max_R - R + min_R
            else:
                physical_R = R

            # The size of the shape is proportional to the local ring spacing
            shape_r = dR * fill

            if dR <= 0:
                continue

            num_items = ring_item_count(ring_shape, physical_R, dR, spokes,
                                        rect_aspect, ring_rot_rad)
            # Phase: twist (absolute fraction of a lap per ring) + stagger
            # (accumulated fraction of each ring's own pitch).
            phase = (twist / 360.0 * i + cumulative_stagger) % 1.0
            cumulative_stagger += stagger / num_items

            positions = ring_positions(
                ring_shape, physical_R, dR, num_items, phase,
                rect_aspect, corners, ring_rot_rad,
            )

            for cx, cy, ori in positions:
                # Angular control: local-normal-relative or global orientation
                if rotation_mode == "global":
                    final_ori = rot_rad
                else:
                    final_ori = ori + rot_rad
                out.append(make_cutout(shape_type, cx, cy, shape_r, final_ori))
        return out

    def _finalize(raw):
        """
        Size control + minimum hole size (resolved together).

        These constraints interact: scaling to a target size changes hole
        sizes, and dropping undersized holes can shrink the pattern extent
        (e.g. when the smallest, densest rings are on the outside with
        --invert). A single scale-then-filter pass could therefore leave the
        pattern smaller than the requested size. We iterate scale -> filter
        until both constraints hold simultaneously. The center hole (fixed,
        final units) is appended afterwards.

        Returns (shapes, dropped_count).
        """
        shp = raw
        drp = 0
        while shp:
            scale = _target_scale(shp, ring_shape, rect_dims, diameter)
            if abs(scale - 1.0) > 1e-12:
                for s in shp:
                    _scale_shape(s, scale)
            if min_hole <= 0:
                break
            kept = [s for s in shp if s["size"] >= min_hole - 1e-9]
            if len(kept) == len(shp):
                break  # stable: everything fits and meets minimum size
            drp += len(shp) - len(kept)
            shp = kept

        # Optional center hole (e.g. fan hub clearance), in final units
        if center_hole > 0:
            if center_shape == "circle":
                center_r = center_hole / 2.0
            else:
                # center_hole is the inscribed diameter; convert to circumradius
                n = 4 if center_shape == "square" else 6
                center_r = (center_hole / 2.0) / math.cos(math.pi / n)
            center = make_cutout(center_shape, 0.0, 0.0, center_r,
                                 math.radians(center_rotation))
            # Fixed-size feature: does not shrink with the fill factor.
            center["fixed"] = True
            shp.append(center)
        return shp, drp

    # ---- Wall thickness constraint: global fill reduction (Option C) ----
    # If the narrowest wall between any two neighboring cutouts (measured in
    # final, scaled units) is thinner than min_wall, uniformly reduce the
    # fill factor and rebuild the entire pattern, repeating until satisfied.
    # This preserves the proportional look everywhere instead of distorting
    # individual rings.
    fill = fill_factor
    shapes, dropped = _finalize(_generate(fill))

    if min_wall > 0 and shapes:
        for _ in range(30):
            worst = _min_gap(shapes)
            if worst is None or worst[0] >= min_wall - 1e-9:
                if abs(fill - fill_factor) > 1e-12:
                    print(f"Note: fill reduced {fill_factor} -> {fill:.4f} "
                          f"to satisfy min wall {min_wall}.")
                break
            _, i, j = worst
            si, sj = shapes[i], shapes[j]
            ci, cj = _shape_center(si), _shape_center(sj)
            d = math.hypot(cj[0] - ci[0], cj[1] - ci[1])
            ri, rj = _outer_radius(si), _outer_radius(sj)
            # Only radii tied to the fill factor can shrink; fixed features
            # (the center hole) keep their size no matter what.
            r_fixed = (ri if si.get("fixed") else 0.0) + (rj if sj.get("fixed") else 0.0)
            r_shrink = (0.0 if si.get("fixed") else ri) + (0.0 if sj.get("fixed") else rj)
            budget = d - min_wall - r_fixed  # room available for shrinkable radii
            if budget <= 0:
                if r_fixed > 0:
                    print(f"Warning: min wall {min_wall} cannot be satisfied — "
                          f"the center hole (r={r_fixed:.2f}) is only {d:.2f} "
                          f"from the nearest ring cutout. Reduce --center-hole "
                          f"or increase --r0.")
                else:
                    print(f"Warning: min wall {min_wall} cannot be satisfied — "
                          f"cutout centers are only {d:.3f} apart at this "
                          f"density. Use fewer --rings, a larger --diameter, "
                          f"or drop small holes with --min-hole.")
                break
            if r_shrink <= 0:
                # Both shapes fixed (shouldn't happen with one center hole)
                print(f"Warning: min wall {min_wall} violated between fixed "
                      f"features; narrowest wall is {worst[0]:.3f}.")
                break
            # Shrink so the worst pair leaves exactly min_wall. Capped
            # slightly below 1 so the loop always makes progress even when
            # rebuild/rescale shifts the estimate.
            shrink = budget / r_shrink
            fill *= min(shrink, 0.98)
            if fill <= 1e-6:
                print("Warning: fill factor collapsed to zero while trying "
                      "to satisfy min wall; check parameters.")
                break
            shapes, dropped = _finalize(_generate(fill))
        else:
            worst = _min_gap(shapes)
            print(f"Warning: min wall {min_wall} not fully satisfied after "
                  f"iteration limit; narrowest wall is {worst[0]:.3f}.")

    if dropped:
        print(f"Note: dropped {dropped} holes smaller than min size {min_hole}.")

    if not shapes:
        print("Warning: no cutouts were generated — check that the ring "
              "spacing is positive (e.g. --factor > 1 for exponential mode) "
              "and that --min-hole isn't dropping every hole.")

    return shapes

def write_dxf(shapes, filename):
    """Write computed shapes to a DXF file."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    for shape in shapes:
        if shape["type"] == "circle":
            msp.add_circle(shape["center"], shape["radius"])
        elif shape["type"] == "polygon":
            msp.add_lwpolyline(shape["points"], close=True)

    doc.saveas(filename)

# Defaults of compute_shapes kwargs, used to keep recorded parameter strings
# short by omitting values the user didn't change.
_PARAM_DEFAULTS = {
    "num_rings": 15, "r0": 10.0, "factor": 1.15, "mode": "exponential",
    "fill_factor": 0.35, "invert": False, "twist": 0.0, "spokes": 0,
    "stagger": 0.0, "ring_shape": "circle", "rect_dims": None,
    "diameter": None, "min_hole": 0.0, "center_hole": 0.0,
    "center_shape": "circle", "rotation": 0.0, "rotation_mode": "normal",
    "corners": False, "ring_rotation": 0.0, "center_rotation": 0.0,
    "min_wall": 0.0,
}

# compute_shapes kwarg name -> CLI flag
_PARAM_TO_FLAG = {
    "shape_type": "--shape", "num_rings": "--rings", "r0": "--r0",
    "factor": "--factor", "mode": "--mode", "fill_factor": "--fill",
    "invert": "--invert", "twist": "--twist", "spokes": "--spokes",
    "stagger": "--stagger", "ring_shape": "--ring-shape",
    "rect_dims": "--rect", "diameter": "--diameter",
    "min_hole": "--min-hole", "center_hole": "--center-hole",
    "center_shape": "--center-shape", "rotation": "--rotation",
    "rotation_mode": "--rotation-mode", "corners": "--corners",
    "ring_rotation": "--ring-rotation", "center_rotation": "--center-rotation",
    "min_wall": "--min-wall",
}

def _fmt_num(v):
    """Compact number formatting: drop trailing .0 on floats."""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)

def params_to_cli(kwargs):
    """
    Render a compute_shapes kwargs dict as the equivalent CLI invocation
    string (only non-default values). This is the canonical way parameters
    are recorded on previews and demo panels so any result is reproducible.
    """
    parts = []
    # Always record the cutout shape explicitly
    if "shape_type" in kwargs:
        parts.append(f"--shape {kwargs['shape_type']}")
    for key, flag in _PARAM_TO_FLAG.items():
        if key == "shape_type":
            continue
        if key not in kwargs:
            continue
        val = kwargs[key]
        if key in _PARAM_DEFAULTS and val == _PARAM_DEFAULTS[key]:
            continue
        if isinstance(val, bool):
            if val:
                parts.append(flag)
        elif key == "rect_dims" and val is not None:
            parts.append(f"{flag} {_fmt_num(val[0])} {_fmt_num(val[1])}")
        elif val is not None:
            parts.append(f"{flag} {_fmt_num(val)}")
    return " ".join(parts)

def args_to_kwargs(args):
    """Map parsed CLI args to a compute_shapes kwargs dict."""
    return dict(
        shape_type=args.shape, num_rings=args.rings, r0=args.r0,
        factor=args.factor, mode=args.mode, fill_factor=args.fill,
        invert=args.invert, twist=args.twist, spokes=args.spokes,
        stagger=args.stagger, ring_shape=args.ring_shape,
        rect_dims=tuple(args.rect) if args.rect else None,
        diameter=args.diameter, min_hole=args.min_hole,
        center_hole=args.center_hole, center_shape=args.center_shape,
        rotation=args.rotation, rotation_mode=args.rotation_mode,
        corners=args.corners, ring_rotation=args.ring_rotation,
        center_rotation=args.center_rotation, min_wall=args.min_wall,
    )

def preview_shapes(shapes, title="", params_str=""):
    """Show an interactive matplotlib preview of the computed shapes.

    params_str: full parameter record (CLI-equivalent string) shown as a
    footer so the preview always documents how it was generated.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Polygon

    fig, ax = plt.subplots(figsize=(8, 8.5))

    for shape in shapes:
        if shape["type"] == "circle":
            patch = Circle(shape["center"], shape["radius"],
                           fill=False, edgecolor="black", linewidth=0.8)
        else:
            patch = Polygon(shape["points"], closed=True,
                            fill=False, edgecolor="black", linewidth=0.8)
        ax.add_patch(patch)

    ax.set_aspect("equal")
    # Autoscale doesn't account for patches automatically; compute bounds manually
    xs, ys = [], []
    for shape in shapes:
        if shape["type"] == "circle":
            cx, cy = shape["center"]
            r = shape["radius"]
            xs.extend([cx - r, cx + r])
            ys.extend([cy - r, cy + r])
        else:
            for px, py in shape["points"]:
                xs.append(px)
                ys.append(py)
    if xs and ys:
        pad = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys))
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)

    ax.set_title(title)
    if params_str:
        import textwrap
        wrapped = "\n".join(textwrap.wrap("perforata.py " + params_str, width=90))
        fig.text(0.5, 0.02, wrapped, ha="center", va="bottom",
                 fontsize=8, family="monospace")
        fig.subplots_adjust(bottom=0.10)
    plt.show()

def _draw_shapes_on_axis(ax, shapes, title="", params_str=""):
    """Draw shapes onto a given matplotlib axis (helper for demo grid).

    params_str: CLI-equivalent parameter record, rendered below the panel.
    """
    import textwrap
    from matplotlib.patches import Circle, Polygon

    for shape in shapes:
        if shape["type"] == "circle":
            patch = Circle(shape["center"], shape["radius"],
                           fill=False, edgecolor="black", linewidth=0.5)
        else:
            patch = Polygon(shape["points"], closed=True,
                            fill=False, edgecolor="black", linewidth=0.5)
        ax.add_patch(patch)

    xs, ys = [], []
    for shape in shapes:
        if shape["type"] == "circle":
            cx, cy = shape["center"]
            r = shape["radius"]
            xs.extend([cx - r, cx + r])
            ys.extend([cy - r, cy + r])
        else:
            for px, py in shape["points"]:
                xs.append(px)
                ys.append(py)
    if xs and ys:
        pad = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys))
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)

    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=10)
    if params_str:
        wrapped = "\n".join(textwrap.wrap(params_str, width=58))
        ax.set_xlabel(wrapped, fontsize=7, family="monospace")

def run_demo(out_image="demo_grid.png", show=False):
    """Render a 3x5 grid of interesting configurations to one large 16:9 image."""
    import matplotlib.pyplot as plt

    # Each entry: (title, kwargs for compute_shapes)
    base = dict(num_rings=15, r0=10.0, factor=1.15, mode="exponential",
                fill_factor=0.35, invert=False)
    demos = [
        ("Hexagons, exponential (default)",
         dict(base, shape_type="hexagon")),
        ("Circles, dense center (inverted)",
         dict(base, shape_type="circle", invert=True)),
        ("Squares, quadratic spacing",
         dict(base, shape_type="square", mode="quadratic", factor=1.5, num_rings=10)),
        ("Circles, linear spacing",
         dict(base, shape_type="circle", mode="linear", factor=6.0)),
        ("Spiral arms: spokes + twist",
         dict(base, shape_type="hexagon", spokes=24, twist=10.0)),
        ("Brickwork stagger 0.5",
         dict(base, shape_type="square", stagger=0.5)),
        ("Hexagonal rings",
         dict(base, shape_type="hexagon", ring_shape="hexagon")),
        ("Square rings, circle cutouts",
         dict(base, shape_type="circle", ring_shape="square")),
        ("120mm fan grill: hub + min hole/wall",
         dict(base, shape_type="circle", num_rings=8, r0=6.0, factor=1.25,
              diameter=120.0, center_hole=40.0, min_hole=3.0, min_wall=2.0)),
        ("Hex rings, inverted density",
         dict(base, shape_type="circle", ring_shape="hexagon", invert=True)),
        ("Square rings + twist spokes",
         dict(base, shape_type="square", ring_shape="square", spokes=32, twist=6.0)),
        ("Rect rings 3:2, staggered circles",
         dict(base, shape_type="circle", ring_shape="rect", rect_dims=(300.0, 200.0), stagger=0.5)),
        ("Linear spacing + brickwork stagger",
         dict(base, shape_type="square", mode="linear", factor=6.0, stagger=0.5)),
        ("Corners mode: hex rings + center hex",
         dict(base, shape_type="hexagon", ring_shape="hexagon", corners=True,
              center_hole=12.0, center_shape="hexagon")),
        ("Global rotation 45°, square cutouts",
         dict(base, shape_type="square", rotation_mode="global", rotation=45.0)),
    ]

    # 16:9 canvas (3200x1800 px at dpi=100)
    fig, axes = plt.subplots(3, 5, figsize=(32, 18))

    for ax, (title, kwargs) in zip(axes.flat, demos):
        shapes = compute_shapes(**kwargs)
        _draw_shapes_on_axis(ax, shapes, title, params_to_cli(kwargs))

    fig.suptitle("perforata.py demo gallery", fontsize=18)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_image, dpi=100)
    print(f"Saved demo gallery to {out_image}")

    if show:
        plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate DXF with variable density cuts.")
    parser.add_argument("--out", default="cuts.dxf", help="Output DXF file name")
    parser.add_argument("--shape", choices=["circle", "square", "hexagon"], default="hexagon", help="Cutout shape")
    parser.add_argument("--ring-shape", choices=["circle", "square", "hexagon", "rect"], default="circle",
                        help="Shape of the concentric rings the cutouts follow")
    parser.add_argument("--rect", type=float, nargs=2, metavar=("W", "H"), default=None,
                        help="Bounding rectangle W H for --ring-shape rect; the pattern is scaled uniformly to fit inside it (the tighter axis matches exactly, the other may come out slightly smaller to preserve cutout proportions)")
    parser.add_argument("--rings", type=int, default=15, help="Number of concentric rings")
    parser.add_argument("--mode", choices=["exponential", "quadratic", "linear"], default="exponential", help="Spacing math mode")
    parser.add_argument("--r0", type=float, default=10.0, help="Radius of the innermost ring")
    parser.add_argument("--factor", type=float, default=1.15, help="Growth factor")
    parser.add_argument("--fill", type=float, default=0.35, help="Shape size relative to space (0.5 max so they don't touch)")
    parser.add_argument("--diameter", type=float, default=None,
                        help="Explicit overall pattern size (outer extent incl. cuts) in drawing units, e.g. 120 for a 120mm fan grill. Pattern is scaled to fit exactly")
    parser.add_argument("--center-hole", type=float, default=0.0,
                        help="Inscribed diameter of an optional cutout at the center (inside the inner ring), in final units. 0 = none")
    parser.add_argument("--center-shape", choices=["circle", "square", "hexagon"], default="circle",
                        help="Shape of the center cutout (used with --center-hole)")
    parser.add_argument("--rotation", type=float, default=0.0,
                        help="Extra rotation (degrees) applied to every cutout shape")
    parser.add_argument("--rotation-mode", choices=["normal", "global"], default="normal",
                        help="'normal': rotation added to each cutout's local outward-normal orientation; 'global': all cutouts share the same absolute angle")
    parser.add_argument("--ring-rotation", type=float, default=0.0,
                        help="Rotation (degrees) of the ring shape itself (concentric polygons, or start angle on circular rings); independent of cutout rotation")
    parser.add_argument("--center-rotation", type=float, default=0.0,
                        help="Rotation (degrees) of the center cutout, independent of ring and cutout rotations")
    parser.add_argument("--corners", action="store_true",
                        help="On non-circular rings, force cutouts on the ring corners and distribute the rest along the sides (ignores twist/stagger/spokes)")
    parser.add_argument("--min-hole", type=float, default=0.0,
                        help="Minimum hole size (inscribed diameter) in final units; smaller holes are dropped. 0 = no cutoff")
    parser.add_argument("--min-wall", type=float, default=0.0,
                        help="Minimum wall thickness between neighboring cutouts in final units; fill is reduced globally until satisfied. 0 = no constraint")
    parser.add_argument("--invert", action="store_true", help="Invert density so smallest, most dense tiles are on the outside")
    parser.add_argument("--twist", type=float, default=0.0, help="Degrees of rotation added per ring, creating a spiral twist between rings")
    parser.add_argument("--spokes", type=int, default=0, help="Force a fixed number of shapes per ring (aligned radial spokes); makes twist clearly visible as spiral arms. 0 = auto density")
    parser.add_argument("--stagger", type=float, default=0.0, help="Fraction of each ring's angular pitch to offset per ring (e.g. 0.5 = brickwork stagger); visible even in auto density mode")
    parser.add_argument("--preview", action="store_true", help="Show a matplotlib preview window of the pattern")
    parser.add_argument("--no-dxf", action="store_true", help="Skip writing the DXF file (useful with --preview)")
    parser.add_argument("--demo", action="store_true", help="Render a 3x5 gallery of interesting configurations to one large image and exit")
    parser.add_argument("--demo-out", default="demo_grid.png", help="Output image path for --demo mode")

    args = parser.parse_args()

    if args.demo:
        run_demo(args.demo_out, show=args.preview)
        raise SystemExit(0)

    if args.ring_shape == "rect" and args.rect is None:
        parser.error("--ring-shape rect requires --rect W H")
    if args.ring_shape == "rect" and args.diameter is not None:
        parser.error("--diameter is not used with --ring-shape rect; size is set by --rect W H")
    if args.mode == "exponential" and args.r0 <= 0:
        parser.error("--mode exponential requires --r0 > 0 (with r0 = 0 every "
                     "ring collapses to radius 0); use --mode linear/quadratic "
                     "for patterns that start at the center")

    kwargs = args_to_kwargs(args)
    shapes = compute_shapes(**kwargs)
    params_str = params_to_cli(kwargs)
    print(f"Parameters: perforata.py {params_str}")

    # Report actual final dimensions and hole size range
    if shapes:
        min_x = min(_shape_bounds(s)[0] for s in shapes)
        min_y = min(_shape_bounds(s)[1] for s in shapes)
        max_x = max(_shape_bounds(s)[2] for s in shapes)
        max_y = max(_shape_bounds(s)[3] for s in shapes)
        sizes = [s["size"] for s in shapes]
        print(f"Pattern extent: {max_x - min_x:.2f} x {max_y - min_y:.2f} units | "
              f"hole sizes: {min(sizes):.2f} to {max(sizes):.2f} (inscribed dia)")

    if not args.no_dxf:
        write_dxf(shapes, args.out)
        print(f"Generated {args.out}: {args.rings} {args.ring_shape} rings of {args.shape}s using {args.mode} spacing ({len(shapes)} cutouts).")

    if args.preview:
        title = f"{args.shape}s | {args.mode} | rings={args.rings} factor={args.factor}"
        if args.ring_shape != "circle":
            title += f" | ring={args.ring_shape}"
        preview_shapes(shapes, title, params_str)
