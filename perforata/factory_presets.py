"""Factory presets: curated pipelines shipped with the repo.

Presets are stored as **UI state** — a dict of Streamlit widget keys and
values matching the controls in ``app.py``. Loading a preset hydrates
the exact same widgets a user would set by hand, so presets and manual
edits flow through one identical code path (no separate "preset
pipeline" that can drift from the UI).

Widget key reference (see app.py):

    mode_radio          "Simple" | "Advanced"
    gen_type            "Cartesian" | "Hexagonal" | "Concentric rings"
    gen_tagmode         "Uniform" | "Alternate rows (even/odd)" |
                        "Major every Nth row"
    gen_*               generator params (px/py/w/h/stag/cstag/fit, p,
                        r/r0/mode/f/...)
    fconv_*             field convolution controls (Simple mode)
    xform_*             transform controls (Simple mode)
    rules_<tag>_*       cutout shape/fill/rotation per tag group
    mfg_*               crop + min hole / min wall / fit-to-size
    adv_steps, step<i>_*  advanced-mode modifier stack

Values must match the widget's value type exactly (float for float
number_inputs/sliders, int for int ones, bool for checkboxes).
"""

from __future__ import annotations

from .presets import register_factory


def _ui(state: dict) -> dict:
    """Wrap a UI-state dict in the standard preset payload."""
    base = {"mode_radio": "Simple"}
    base.update(state)
    return {"ui_state": base}


@register_factory("honeycomb-vent")
def honeycomb_vent():
    """Hex lattice of hexagons — a dense honeycomb ventilation panel.

    fill is the inscribed diameter vs pitch, so 0.8 leaves 20% of the
    9mm pitch as wall (~1.8mm, above the 1.5mm minimum). fit=True snaps
    the lattice to the panel so edge margins are balanced.
    """
    return _ui({
        "gen_type": "Hexagonal",
        "gen_tagmode": "Uniform",
        "gen_p": 9.0, "gen_w": 250.0, "gen_h": 180.0,
        "gen_fit": True,
        "rules_all_shape": "hexagon", "rules_all_fill": 0.8,
        "rules_all_rot": 0.0, "rules_all_abs": False,
        "mfg_crop_on": True, "mfg_crop_kind": "Rectangle",
        "mfg_crop_w": 250.0, "mfg_crop_h": 180.0,
        "mfg_crop_mode": "cull", "mfg_crop_m": 0.0, "mfg_crop_out": True,
        "mfg_minh": 0.0, "mfg_minw": 1.5, "mfg_fit": 0.0,
    })


@register_factory("classic-fan-grill-120mm")
def classic_fan_grill():
    """Circles on concentric rings sized for a 120mm PC fan.

    Geometry is chosen so the manufacturing constraints hold without
    decimating the pattern: with fill=0.7 the wall between neighbors is
    ~0.3x the ring gap, so a ~7mm gap yields ~4.9mm holes and ~2.1mm
    walls — comfortably above the 3mm/2mm minimums after scaling.
    """
    return _ui({
        "gen_type": "Concentric rings",
        "gen_tagmode": "Uniform",
        "gen_r": 6, "gen_r0": 20.0, "gen_mode": "linear", "gen_f": 7.0,
        "gen_tw": 0.0, "gen_sp": 0, "gen_st": 0.0, "gen_inv": False,
        "rules_all_shape": "circle", "rules_all_fill": 0.7,
        "rules_all_rot": 0.0, "rules_all_abs": False,
        "mfg_minh": 3.0, "mfg_minw": 2.0, "mfg_fit": 120.0,
    })


@register_factory("triangle-flip-panel")
def triangle_flip_panel():
    """Cartesian grid with triangles alternating orientation row by
    row — the classic major/minor tessellation.

    Triangle fill counts the inscribed diameter (the apothem-based
    narrow opening), and a triangle's circumradius is 2x its inradius —
    so 0.45 here matches the visual density the old circumradius-based
    0.85 produced, without neighboring triangles overlapping.
    """
    return _ui({
        "gen_type": "Cartesian",
        "gen_tagmode": "Alternate rows (even/odd)",
        "gen_px": 10.0, "gen_py": 9.0,
        "gen_w": 300.0, "gen_h": 200.0,
        "gen_stag": 0.5, "gen_cstag": 0.0, "gen_fit": True,
        "rules_even_shape": "triangle", "rules_even_fill": 0.45,
        "rules_even_rot": 90.0, "rules_even_abs": False,
        "rules_odd_shape": "triangle", "rules_odd_fill": 0.45,
        "rules_odd_rot": -90.0, "rules_odd_abs": False,
        "mfg_crop_on": True, "mfg_crop_kind": "Rectangle",
        "mfg_crop_w": 300.0, "mfg_crop_h": 200.0,
        "mfg_crop_mode": "cull", "mfg_crop_m": 0.0, "mfg_crop_out": True,
    })


@register_factory("radial-burst")
def radial_burst():
    """Cartesian circles convolved with a circular exponential gradient:
    big holes in the middle, shrinking outward, sparse edges dropped."""
    return _ui({
        "gen_type": "Cartesian",
        "gen_tagmode": "Uniform",
        "gen_px": 8.0, "gen_py": 8.0, "gen_w": 200.0, "gen_h": 200.0,
        "gen_stag": 0.0, "gen_cstag": 0.0, "gen_fit": False,
        "fconv_on": True,
        "fconv_src": "Shape gradient",
        "fconv_gshape": "circle", "fconv_gfall": "exponential",
        "fconv_gk": 3.0, "fconv_ginv": True,
        "fconv_target": "Cutout size",
        "fconv_lo": 0.1, "fconv_hi": 1.0, "fconv_drop": 0.12,
        "rules_all_shape": "circle", "rules_all_fill": 0.8,
        "rules_all_rot": 0.0, "rules_all_abs": False,
        "mfg_crop_on": True, "mfg_crop_kind": "Circle",
        "mfg_crop_d": 200.0, "mfg_crop_mode": "cull",
        "mfg_crop_m": 0.0, "mfg_crop_out": True,
    })


@register_factory("density-gradient-screen")
def density_gradient_screen():
    """Hex grid whose spacing compresses along a linear gradient —
    dense on the right, open on the left."""
    return _ui({
        "gen_type": "Hexagonal",
        "gen_tagmode": "Uniform",
        "gen_p": 10.0, "gen_w": 300.0, "gen_h": 150.0,
        "fconv_on": True,
        "fconv_src": "Linear gradient", "fconv_ang": 0.0,
        "fconv_target": "Grid spacing (density)", "fconv_dstr": 0.8,
        "rules_all_shape": "circle", "rules_all_fill": 0.7,
        "rules_all_rot": 0.0, "rules_all_abs": False,
    })


@register_factory("monogram-letter-A")
def monogram_letter():
    """Hex grid where a rendered letter "A" drives the cutout sizes:
    big holes spell out the glyph, tiny holes fill the background —
    complex image convolution working straight out of the box."""
    return _ui({
        "gen_type": "Hexagonal",
        "gen_tagmode": "Uniform",
        "gen_p": 6.0, "gen_w": 200.0, "gen_h": 200.0,
        "fconv_on": True,
        "fconv_src": "Text / letter",
        "fconv_txt": "A", "fconv_txtbold": True, "fconv_txtinv": True,
        "fconv_txtblur": 0.02,
        "fconv_target": "Cutout size",
        "fconv_lo": 0.2, "fconv_hi": 1.0, "fconv_drop": 0.0,
        "rules_all_shape": "circle", "rules_all_fill": 0.8,
        "rules_all_rot": 0.0, "rules_all_abs": False,
        "mfg_crop_on": True, "mfg_crop_kind": "Rectangle",
        "mfg_crop_w": 200.0, "mfg_crop_h": 200.0,
        "mfg_crop_mode": "cull", "mfg_crop_m": 0.0, "mfg_crop_out": True,
    })


@register_factory("sphere-relief")
def sphere_relief():
    """Hex grid of hexagons sized by a spherical height profile —
    the cutouts shade a 3D ball bulging out of the panel.

    The "sphere" falloff is sqrt(1 - d^2) when inverted (strong at
    center): the actual height of a hemisphere over the panel, so the
    shading reads as a physically-lit ball rather than a generic
    radial fade. Hexagon cutouts on the hex lattice keep walls uniform
    at every size, and drop=0.1 trims the vanishing holes just outside
    the sphere's silhouette.
    """
    return _ui({
        "gen_type": "Hexagonal",
        "gen_tagmode": "Uniform",
        "gen_p": 7.0, "gen_w": 220.0, "gen_h": 220.0,
        "gen_fit": True,
        "fconv_on": True,
        "fconv_src": "Shape gradient",
        "fconv_gshape": "circle", "fconv_gfall": "sphere",
        "fconv_ginv": True,
        "fconv_target": "Cutout size",
        "fconv_lo": 0.1, "fconv_hi": 1.0, "fconv_drop": 0.1,
        "rules_all_shape": "hexagon", "rules_all_fill": 0.8,
        "rules_all_rot": 0.0, "rules_all_abs": False,
        "mfg_crop_on": True, "mfg_crop_kind": "Rectangle",
        "mfg_crop_w": 220.0, "mfg_crop_h": 220.0,
        "mfg_crop_mode": "cull", "mfg_crop_m": 0.0, "mfg_crop_out": True,
    })


@register_factory("spiral-galaxy")
def spiral_galaxy():
    """Concentric rings with forced spokes and per-ring twist: visible
    spiral arms of hexagons.

    Hexagon fill is the inscribed diameter vs the ring gap: 0.6 matches
    the openness the old circumradius-based 0.7 gave (0.7 * cos 30°).
    """
    return _ui({
        "gen_type": "Concentric rings",
        "gen_tagmode": "Uniform",
        "gen_r": 15, "gen_r0": 10.0, "gen_mode": "exponential",
        "gen_f": 1.15, "gen_tw": 10.0, "gen_sp": 24, "gen_st": 0.0,
        "gen_inv": False,
        "rules_all_shape": "hexagon", "rules_all_fill": 0.6,
        "rules_all_rot": 0.0, "rules_all_abs": False,
    })
