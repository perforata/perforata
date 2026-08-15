"""Tests of the params-dict API (perforata.api) — the shared contract
for the CLI and the web platform."""

import json

import pytest

from perforata import api

HEX_PARAMS = {
    "version": 2,
    "generator": {"type": "HexGrid", "pitch": 10.0,
                  "width": 100.0, "height": 80.0},
    "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
    "manufacturing": {"min_wall": 1.0},
}

FULL_PARAMS = {
    "version": 2,
    "generator": {"type": "CartesianGrid",
                  "pitch_x": 10.0, "pitch_y": 10.0,
                  "width": 100.0, "height": 100.0},
    "modifiers": [
        {"type": "Rotate", "degrees": 15.0},
        {"type": "FieldModulate",
         "attr": "scale",
         "field": {"type": "RadialGradient", "invert": True},
         "lo": 0.2, "hi": 1.0},
        {"type": "AttributeFilter", "attr": "scale", "lo": 0.1},
    ],
    "rules": {"*": {"shape": "circle", "fill": 0.7}},
    "manufacturing": {
        "crop": {"boundary": {"kind": "circle", "diameter": 90.0},
                 "mode": "cull", "outline": True},
    },
}

# The same hex pipeline in the legacy v1 (nested-"params") layout.
HEX_PARAMS_V1 = {
    "v": 1,
    "generator": {"type": "HexGrid",
                  "params": {"pitch": 10.0, "width": 100.0,
                             "height": 80.0}},
    "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
    "manufacturing": {"min_wall": 1.0},
}


def test_evaluate_returns_shapes_and_stats():
    result = api.evaluate(HEX_PARAMS)
    assert result["stats"]["cutouts"] > 0
    assert result["stats"]["points"] >= result["stats"]["cutouts"]
    assert len(result["shapes"]) >= result["stats"]["cutouts"]
    assert "total" in result["timings_ms"]
    # Every shape is in the compact JSON form
    for s in result["shapes"]:
        assert s["t"] in ("c", "p")


def test_evaluate_full_pipeline_with_modifiers_and_crop():
    result = api.evaluate(FULL_PARAMS)
    assert result["stats"]["cutouts"] > 0
    # Crop outline present as a non-cutout shape
    outlines = [s for s in result["shapes"] if s["o"]]
    assert len(outlines) == 1


def test_evaluate_json_roundtrip():
    out = json.loads(api.evaluate_json(json.dumps(HEX_PARAMS)))
    assert out["stats"]["cutouts"] > 0


def test_evaluate_result_is_json_safe():
    """No numpy scalars may leak into the result (stats included)."""
    result = api.evaluate(HEX_PARAMS)
    json.dumps(result)  # raises TypeError on np.float64 leakage
    for v in result["stats"].values():
        assert type(v) in (int, float, list)


def test_evaluate_auto_migrates_v1_with_warning():
    with pytest.warns(DeprecationWarning, match="schema v1"):
        result = api.evaluate(HEX_PARAMS_V1)
    assert result["stats"]["cutouts"] > 0
    # Identical geometry to the equivalent v2 document
    v2 = api.evaluate(HEX_PARAMS)
    assert result["stats"]["cutouts"] == v2["stats"]["cutouts"]


def test_export_svg_and_dxf():
    svg = api.export(HEX_PARAMS, "svg")
    assert svg.startswith(b"<svg")
    dxf = api.export(HEX_PARAMS, "dxf")
    assert b"CIRCLE" in dxf or b"LWPOLYLINE" in dxf


def test_export_unknown_format():
    with pytest.raises(ValueError, match="unknown export format"):
        api.export(HEX_PARAMS, "png")


def test_validate_ok():
    assert api.validate(HEX_PARAMS) == []
    assert api.validate(FULL_PARAMS) == []
    # v1 documents auto-migrate and validate silently
    assert api.validate(HEX_PARAMS_V1) == []


def test_validate_catches_problems():
    assert api.validate("nope") == ["params must be a JSON object"]
    assert any("generator" in p for p in api.validate({}))
    assert any("generator" in p and "Nope" in p for p in api.validate(
        {"generator": {"type": "Nope"}}))
    bad_mod = dict(HEX_PARAMS, modifiers=[{"type": "Bogus"}])
    assert any(p.startswith("modifiers.0") for p in api.validate(bad_mod))
    newer = dict(HEX_PARAMS, version=999)
    assert any("schema version" in p for p in api.validate(newer))


def test_validate_rejects_extra_keys():
    typo = dict(HEX_PARAMS,
                generator={"type": "HexGrid", "pich": 10.0})
    problems = api.validate(typo)
    assert any("pich" in p for p in problems)


def test_validate_rejects_out_of_range_fill():
    bad = dict(HEX_PARAMS, rules={"*": {"shape": "hexagon", "fill": 1.5}})
    problems = api.validate(bad)
    assert any("fill" in p for p in problems)
