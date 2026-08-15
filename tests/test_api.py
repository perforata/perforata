"""Tests of the params-dict API (perforata.api) — the shared contract
for the CLI and the web platform."""

import json

import pytest

from perforata import api

HEX_PARAMS = {
    "v": 1,
    "generator": {"type": "HexGrid",
                  "params": {"pitch": 10.0, "width": 100.0,
                             "height": 80.0}},
    "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
    "manufacturing": {"min_wall": 1.0},
}

FULL_PARAMS = {
    "v": 1,
    "generator": {"type": "CartesianGrid",
                  "params": {"pitch_x": 10.0, "pitch_y": 10.0,
                             "width": 100.0, "height": 100.0}},
    "modifiers": [
        {"type": "Rotate", "params": {"degrees": 15.0}},
        {"type": "FieldModulate",
         "params": {"attr": "scale",
                    "field": {"type": "RadialGradient",
                              "params": {"invert": True}},
                    "lo": 0.2, "hi": 1.0}},
        {"type": "AttributeFilter", "params": {"attr": "scale", "lo": 0.1}},
    ],
    "rules": {"*": {"shape": "circle", "fill": 0.7}},
    "manufacturing": {
        "crop": {"boundary": {"kind": "circle", "diameter": 90.0},
                 "mode": "cull", "outline": True},
    },
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


def test_validate_catches_problems():
    assert api.validate("nope") == ["params must be a JSON object"]
    assert any("generator" in p for p in api.validate({}))
    assert any("unknown generator type" in p for p in api.validate(
        {"generator": {"type": "Nope"}}))
    bad_mod = dict(HEX_PARAMS, modifiers=[{"type": "Bogus"}])
    assert any("modifiers[0]" in p for p in api.validate(bad_mod))
    newer = dict(HEX_PARAMS, v=999)
    assert any("schema version" in p for p in api.validate(newer))
