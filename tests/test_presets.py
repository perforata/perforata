"""Tests of JSON preset storage and rejection of legacy pickle presets."""

import json
import pickle
import sys

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


def test_legacy_pickle_bytes_rejected():
    """Pickle bytes must never be deserialized — CWE-502. No shim, no
    opt-in: loads() rejects anything that isn't JSON outright."""
    legacy = pickle.dumps({
        "format": "perforata-preset", "format_version": 1,
        "app_version": "0.2.0", "payload": PAYLOAD})
    with pytest.raises(ValueError, match="no longer supported"):
        presets.loads(legacy)


def test_legacy_pfp_file_not_listed_or_loadable(tmp_path):
    """A leftover .pfp file from a pre-1.0 install is neither surfaced
    by list_presets() nor picked up by load() — only *.json counts, and
    there is no fallback to a same-named .pfp file."""
    (tmp_path / "old.pfp").write_bytes(pickle.dumps({"payload": PAYLOAD}))
    assert presets.list_presets(directory=tmp_path) == []
    with pytest.raises(FileNotFoundError):
        presets.load("old", directory=tmp_path)


# ----------------------------------------------------------------------
# Path-traversal guard (_path_for): user-controlled preset names must
# never produce a path outside the preset directory.
# ----------------------------------------------------------------------

TRAVERSAL_NAMES = [
    "../evil",
    "../../evil",
    "sub/../../evil",
    "../evil.json",
]
if sys.platform == "win32":
    TRAVERSAL_NAMES += [
        "..\\evil",            # backslash separator
        "\\evil",              # rooted (drive-less) path
        "D:evil",              # drive-relative path on another drive
    ]


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
@pytest.mark.parametrize("op", ["save", "load", "delete"])
def test_traversal_names_rejected(tmp_path, op, name):
    base = tmp_path / "presets"
    base.mkdir()
    with pytest.raises(ValueError,
                       match="escapes preset directory|relative file name"):
        if op == "save":
            presets.save(name, PAYLOAD, directory=base)
        elif op == "load":
            presets.load(name, directory=base)
        else:
            presets.delete(name, directory=base)


def test_absolute_name_rejected(tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_bytes(presets.dumps(PAYLOAD))
    base = tmp_path / "presets"
    base.mkdir()
    for op in (lambda: presets.save(str(victim), PAYLOAD, directory=base),
               lambda: presets.load(str(victim), directory=base),
               lambda: presets.delete(str(victim), directory=base)):
        with pytest.raises(ValueError, match="relative file name"):
            op()
    assert victim.exists()


def test_traversal_cannot_touch_outside_files(tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_bytes(presets.dumps(PAYLOAD))
    base = tmp_path / "presets"
    base.mkdir()
    with pytest.raises(ValueError):
        presets.delete("../victim", directory=base)
    with pytest.raises(ValueError):
        presets.save("../victim", {"ui_state": {}}, directory=base)
    assert presets.loads(victim.read_bytes()) == PAYLOAD  # untouched


def test_subdirectory_names_stay_inside(tmp_path):
    path = presets.save("collection/pattern", PAYLOAD, directory=tmp_path)
    assert path.is_relative_to(tmp_path.resolve())
    assert presets.load("collection/pattern", directory=tmp_path) == PAYLOAD


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX symlink semantics")
def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "presets"
    base.mkdir()
    (base / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes preset directory")
    with pytest.raises(ValueError, match="escapes preset directory"):
        presets.save("link/evil", PAYLOAD, directory=base)


def test_factory_presets_are_json_safe():
    """Every factory preset payload must survive a JSON round-trip —
    the invariant that lets presets ship as data instead of pickle."""
    for name in presets.list_factory():
        payload = presets.load_factory(name)
        assert json.loads(json.dumps(payload)) == payload, name
