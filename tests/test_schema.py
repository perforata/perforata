"""Tests of the typed params schema (perforata.schema)."""

import base64
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from perforata.schema import (SCHEMA_VERSION, PipelineDef, detect_version,
                              migrate)

SNAPSHOT = Path(__file__).parent / "data" / "pipeline.schema.json"

V2_DOC = {
    "version": 2,
    "generator": {"type": "HexGrid", "pitch": 9.0,
                  "width": 250.0, "height": 180.0, "fit": True},
    "modifiers": [
        {"type": "FieldModulate",
         "field": {"type": "ShapeGradient", "shape": "circle",
                   "falloff": "exponential", "k": 3.0, "invert": True},
         "lo": 0.1, "hi": 1.0},
        {"type": "AttributeFilter", "attr": "scale", "lo": 0.12},
    ],
    "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
    "manufacturing": {
        "min_wall": 1.5,
        "crop": {"boundary": {"kind": "rect", "width": 250.0,
                              "height": 180.0},
                 "mode": "cull", "margin": 0.0, "outline": True},
    },
}

V1_DOC = {
    "v": 1,
    "generator": {"type": "HexGrid",
                  "params": {"pitch": 9.0, "width": 250.0,
                             "height": 180.0, "fit": True}},
    "modifiers": [
        {"type": "FieldModulate",
         "params": {"field": {"type": "ShapeGradient",
                              "params": {"shape": "circle",
                                         "falloff": "exponential",
                                         "k": 3.0, "invert": True}},
                    "lo": 0.1, "hi": 1.0}},
        {"type": "AttributeFilter", "params": {"attr": "scale",
                                               "lo": 0.12}},
    ],
    "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
    "manufacturing": {
        "min_wall": 1.5,
        "crop": {"boundary": {"kind": "rect", "width": 250.0,
                              "height": 180.0},
                 "mode": "cull", "margin": 0.0, "outline": True},
    },
}


# -- round trips ---------------------------------------------------------

def test_roundtrip_model_dump():
    pipeline = PipelineDef.model_validate(V2_DOC)
    dumped = pipeline.model_dump(mode="json")
    assert PipelineDef.model_validate(dumped) == pipeline


def test_roundtrip_json():
    pipeline = PipelineDef.model_validate_json(json.dumps(V2_DOC))
    again = PipelineDef.model_validate_json(pipeline.model_dump_json())
    assert again == pipeline


def test_build_produces_engine_nodes():
    pipeline = PipelineDef.model_validate(V2_DOC)
    from perforata.generators import HexGrid
    from perforata.modifiers import FieldModulate, AttributeFilter
    gen = pipeline.generator.build()
    assert isinstance(gen, HexGrid)
    mods = [m.build() for m in pipeline.modifiers]
    assert isinstance(mods[0], FieldModulate)
    assert isinstance(mods[1], AttributeFilter)
    # ...and they actually run
    cloud = gen.run()
    for m in mods:
        cloud = m.run(cloud)
    assert len(cloud) > 0


# -- negative, table-driven ----------------------------------------------

@pytest.mark.parametrize("doc, needle", [
    # wrong discriminator
    ({"generator": {"type": "Nope"}}, "generator"),
    # missing generator entirely
    ({}, "generator"),
    # unknown modifier type
    (dict(V2_DOC, modifiers=[{"type": "Bogus"}]), "modifiers"),
    # unknown field type inside a modifier
    (dict(V2_DOC, modifiers=[{"type": "FieldModulate",
                              "field": {"type": "Wat"}}]), "field"),
    # out-of-range fill
    (dict(V2_DOC, rules={"*": {"fill": 2.0}}), "fill"),
    # extra (typo'd) key
    (dict(V2_DOC, generator={"type": "HexGrid", "pich": 9.0}), "pich"),
    # bad crop boundary kind
    (dict(V2_DOC, manufacturing={
        "crop": {"boundary": {"kind": "blob"}}}), "boundary"),
    # negative manufacturing values
    (dict(V2_DOC, manufacturing={"min_wall": -1.0}), "min_wall"),
    # ImageField: malformed base64
    (dict(V2_DOC, modifiers=[{"type": "FieldModulate",
                              "field": {"type": "ImageField",
                                        "luminance_b64": "not-base64!!",
                                        "w": 2, "h": 2}}]),
     "luminance_b64"),
    # ImageField: payload length doesn't match w*h
    (dict(V2_DOC, modifiers=[{"type": "FieldModulate",
                              "field": {"type": "ImageField",
                                        "luminance_b64": base64.b64encode(
                                            bytes([0, 255, 128])).decode(),
                                        "w": 2, "h": 2}}]),
     "luminance_b64"),
])
def test_invalid_documents_rejected(doc, needle):
    with pytest.raises(ValidationError) as exc:
        PipelineDef.model_validate(doc)
    errors = exc.value.errors()
    locs = ".".join(".".join(str(p) for p in e["loc"]) for e in errors)
    msgs = " ".join(e["msg"] for e in errors)
    assert needle in locs or needle in msgs


# -- field offset & ImageField --------------------------------------------

def test_field_offset_shifts_hotspot_through_schema():
    """offset_u/offset_v in a field def shift the built field's peak."""
    doc = dict(V2_DOC, modifiers=[
        {"type": "FieldModulate",
         "field": {"type": "RadialGradient", "invert": True,
                   "offset_u": 0.2, "offset_v": -0.1},
         "lo": 0.0, "hi": 1.0},
    ])
    pipeline = PipelineDef.model_validate(doc)
    field = pipeline.modifiers[0].field.build()
    base = field.sample(np.array([[0.5, 0.5]]))[0]
    moved = field.sample(np.array([[0.7, 0.4]]))[0]  # 0.5+0.2, 0.5-0.1
    assert moved > base
    assert abs(moved - 1.0) < 1e-9  # recovers the un-shifted peak


def test_image_field_roundtrip_from_synthetic_grid():
    """A small synthetic luminance grid, base64-encoded exactly as the
    web frontend ships it, builds a working ImageField."""
    img = np.zeros((4, 4), dtype=np.uint8)
    img[:, 2:] = 255  # right half white
    b64 = base64.b64encode(img.tobytes()).decode("ascii")
    doc = dict(V2_DOC, modifiers=[
        {"type": "FieldModulate",
         "field": {"type": "ImageField", "luminance_b64": b64,
                   "w": 4, "h": 4, "invert": False, "fit": "stretch"},
         "lo": 0.0, "hi": 1.0},
    ])
    pipeline = PipelineDef.model_validate(doc)
    field = pipeline.modifiers[0].field.build()
    v = field.sample(np.array([[0.1, 0.5], [0.9, 0.5]]))
    assert v[0] < 0.2 and v[1] > 0.8


# -- version detection & migration ---------------------------------------

def test_detect_version():
    assert detect_version(V1_DOC) == 1
    assert detect_version(V2_DOC) == 2
    # nested "params" marks v1 even without an explicit "v"
    unversioned_v1 = {k: v for k, v in V1_DOC.items() if k != "v"}
    assert detect_version(unversioned_v1) == 1
    # flat, unversioned docs default to current
    unversioned_v2 = {k: v for k, v in V2_DOC.items() if k != "version"}
    assert detect_version(unversioned_v2) == SCHEMA_VERSION


def test_migrate_v1_to_v2():
    migrated = migrate(V1_DOC)
    assert migrated["version"] == SCHEMA_VERSION
    assert "v" not in migrated
    # the migrated document validates and equals the hand-written v2 doc
    assert (PipelineDef.model_validate(migrated)
            == PipelineDef.model_validate(V2_DOC))


def test_migrate_v2_is_noop():
    migrated = migrate(V2_DOC)
    assert (PipelineDef.model_validate(migrated)
            == PipelineDef.model_validate(V2_DOC))


def test_migrate_v1_defaults_crop_kind_to_rect():
    doc = {
        "v": 1,
        "generator": {"type": "HexGrid", "params": {}},
        "manufacturing": {"crop": {"boundary": {"width": 100.0,
                                                "height": 50.0}}},
    }
    migrated = migrate(doc)
    assert migrated["manufacturing"]["crop"]["boundary"]["kind"] == "rect"
    PipelineDef.model_validate(migrated)


# -- schema snapshot -------------------------------------------------------

def test_json_schema_snapshot():
    """The committed JSON Schema must match the models. If this fails,
    regenerate it (``perforata schema -o tests/data/pipeline.schema.json``)
    and bump SCHEMA_VERSION if the change is breaking — downstream
    consumers codegen types from this file."""
    current = PipelineDef.model_json_schema()
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == committed
