"""Tests of JSON preset storage and the legacy .pfp deprecation shim."""

import json

import pytest

from perforata import presets

PAYLOAD = {"ui_state": {"gen_type": "Hexagonal", "gen_p": 9.0,
                        "gen_fit": True}}


def test_dumps_is_versioned_json():
    data = presets.dumps(PAYLOAD)
    env = json.loads(data.decode("utf-8"))
    assert env["format"] == "perforata-preset"
    assert env["v"] == presets.FORMAT_VERSION
    assert env["payload"] == PAYLOAD


def test_roundtrip():
    assert presets.loads(presets.dumps(PAYLOAD)) == PAYLOAD


def test_save_load_list_delete(tmp_path):
    path = presets.save("my-pattern", PAYLOAD, directory=tmp_path)
    assert path.suffix == ".json"
    assert presets.list_presets(directory=tmp_path) == ["my-pattern"]
    assert presets.load("my-pattern", directory=tmp_path) == PAYLOAD
    assert presets.delete("my-pattern", directory=tmp_path) is True
    assert presets.list_presets(directory=tmp_path) == []


def test_rejects_non_preset_json():
    with pytest.raises(ValueError, match="not a perforata preset"):
        presets.loads(b'{"hello": "world"}')


def test_rejects_newer_format():
    env = {"format": "perforata-preset", "v": presets.FORMAT_VERSION + 1,
           "payload": {}}
    with pytest.raises(ValueError, match="newer version"):
        presets.loads(json.dumps(env).encode())


def test_legacy_pfp_reads_with_deprecation_warning(tmp_path):
    cloudpickle = pytest.importorskip("cloudpickle")
    legacy = cloudpickle.dumps({
        "format": "perforata-preset", "format_version": 1,
        "app_version": "0.2.0", "payload": PAYLOAD})
    (tmp_path / "old.pfp").write_bytes(legacy)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        assert presets.loads(legacy) == PAYLOAD
    # Listed and loadable by name through the shim
    assert "old" in presets.list_presets(directory=tmp_path)
    with pytest.warns(DeprecationWarning):
        assert presets.load("old", directory=tmp_path) == PAYLOAD


def test_factory_presets_are_json_safe():
    """Every factory preset payload must survive a JSON round-trip —
    the invariant that lets presets ship as data instead of pickle."""
    for name in presets.list_factory():
        payload = presets.load_factory(name)
        assert json.loads(json.dumps(payload)) == payload, name
