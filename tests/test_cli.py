"""Tests of the unified CLI (perforata.cli)."""

import json

import pytest

from perforata import __version__
from perforata.cli import main

PARAMS = {
    "version": 2,
    "generator": {"type": "HexGrid", "pitch": 10.0,
                  "width": 100.0, "height": 80.0},
    "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
}


@pytest.fixture
def pipeline_file(tmp_path):
    p = tmp_path / "pipeline.json"
    p.write_text(json.dumps(PARAMS))
    return p


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_render_svg(pipeline_file, tmp_path, capsys):
    out = tmp_path / "out.svg"
    assert main(["render", str(pipeline_file), "-o", str(out)]) == 0
    assert out.read_bytes().startswith(b"<svg")
    assert "cutouts" in capsys.readouterr().out


def test_render_dxf(pipeline_file, tmp_path):
    out = tmp_path / "out.dxf"
    assert main(["render", str(pipeline_file), "-o", str(out)]) == 0
    assert out.stat().st_size > 0


def test_render_rejects_bad_extension(pipeline_file, tmp_path):
    with pytest.raises(SystemExit):
        main(["render", str(pipeline_file), "-o", str(tmp_path / "o.png")])


def test_render_rejects_invalid_params(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"generator": {"type": "Nope"}}))
    assert main(["render", str(bad), "-o", str(tmp_path / "o.svg")]) == 2
    assert "invalid" in capsys.readouterr().err


def test_validate_ok(pipeline_file, capsys):
    assert main(["validate", str(pipeline_file)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "schema v2" in out


def test_validate_reports_v1(tmp_path, capsys):
    v1 = tmp_path / "v1.json"
    v1.write_text(json.dumps({
        "v": 1,
        "generator": {"type": "HexGrid", "params": {"pitch": 10.0}},
    }))
    assert main(["validate", str(v1)]) == 0
    assert "schema v1" in capsys.readouterr().out


def test_schema_stdout(capsys):
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "PipelineDef"
    assert "generator" in schema["properties"]


def test_schema_to_file(tmp_path, capsys):
    out = tmp_path / "pipeline.schema.json"
    assert main(["schema", "-o", str(out)]) == 0
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["title"] == "PipelineDef"


def test_validate_missing_file():
    with pytest.raises(SystemExit):
        main(["validate", "no-such-file.json"])


def test_presets_list(capsys):
    assert main(["presets", "list"]) == 0
    out = capsys.readouterr().out
    assert "honeycomb-vent" in out


def test_presets_show(capsys):
    assert main(["presets", "show", "honeycomb-vent"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "ui_state" in payload


def test_presets_show_unknown():
    with pytest.raises(SystemExit):
        main(["presets", "show", "not-a-preset"])


def test_demo(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    out = tmp_path / "gallery.png"
    assert main(["demo", "-o", str(out)]) == 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
