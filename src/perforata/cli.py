"""Unified command-line interface (stdlib argparse only).

Installed as the ``perforata`` console script::

    perforata render  pipeline.json -o out.svg      # params JSON -> file
    perforata validate pipeline.json                # schema-check params
    perforata presets list                          # factory presets
    perforata presets show honeycomb-vent
    perforata demo -o gallery.png                   # needs [render]
    perforata ui                                    # needs [app]
    perforata --version

Each subcommand imports its dependency tier lazily and fails with a
friendly "pip install perforata[<extra>]" message when an extra is
missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_params(path: str) -> dict:
    try:
        # utf-8-sig tolerates the BOM PowerShell prepends to UTF-8 files
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        sys.exit(f"error: file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {path} is not valid JSON: {exc}")


def _cmd_render(args) -> int:
    from . import api

    params = _load_params(args.pipeline)
    problems = api.validate(params)
    if problems:
        for p in problems:
            print(f"invalid: {p}", file=sys.stderr)
        return 2

    out = Path(args.output)
    fmt = out.suffix.lstrip(".").lower()
    if fmt not in ("svg", "dxf"):
        sys.exit(f"error: unsupported output extension {out.suffix!r} "
                 "(expected .svg or .dxf)")
    data = api.export(params, fmt)
    if isinstance(data, str):  # exporter returned a path it already wrote
        print(data)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    result = api.evaluate(params)
    stats = result["stats"]
    print(f"wrote {out} ({stats['cutouts']} cutouts, "
          f"{stats['points']} points)")
    return 0


def _cmd_validate(args) -> int:
    from . import api

    params = _load_params(args.pipeline)
    problems = api.validate(params)
    if problems:
        for p in problems:
            print(f"invalid: {p}", file=sys.stderr)
        return 2
    print(f"{args.pipeline}: OK")
    return 0


def _cmd_presets(args) -> int:
    from . import presets

    if args.action == "list":
        for name in presets.list_factory():
            print(name)
        return 0
    # show
    if not args.name:
        sys.exit("error: 'presets show' requires a preset name")
    try:
        payload = presets.load_factory(args.name)
    except KeyError:
        names = ", ".join(presets.list_factory())
        sys.exit(f"error: unknown preset {args.name!r} (available: {names})")
    print(json.dumps(payload, indent=2, sort_keys=True, default=repr))
    return 0


def _cmd_demo(args) -> int:
    from ._deps import require
    require("matplotlib", "render")
    from .demo import gallery_png

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gallery_png())
    print(f"wrote {out}")
    return 0


def _cmd_ui(_args) -> int:
    from ._deps import require
    require("streamlit", "app")
    from streamlit.web import cli as stcli

    app_path = Path(__file__).parent / "app.py"
    sys.argv = ["streamlit", "run", str(app_path)]
    return stcli.main()


def main(argv: list[str] | None = None) -> int:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="perforata",
        description="Parametric perforation pattern generator.")
    parser.add_argument("--version", action="version",
                        version=f"perforata {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("render",
                       help="run a params-JSON pipeline and export a file")
    p.add_argument("pipeline", help="path to a pipeline params JSON file")
    p.add_argument("-o", "--output", required=True,
                   help="output file (.svg or .dxf)")
    p.set_defaults(fn=_cmd_render)

    p = sub.add_parser("validate",
                       help="schema-check a params-JSON pipeline")
    p.add_argument("pipeline", help="path to a pipeline params JSON file")
    p.set_defaults(fn=_cmd_validate)

    p = sub.add_parser("presets", help="inspect factory presets")
    p.add_argument("action", choices=["list", "show"])
    p.add_argument("name", nargs="?",
                   help="preset name (for 'show')")
    p.set_defaults(fn=_cmd_presets)

    p = sub.add_parser("demo",
                       help="render the factory-preset demo gallery "
                            "(requires perforata[render])")
    p.add_argument("-o", "--output", default="demo_gallery.png",
                   help="output PNG path (default: demo_gallery.png)")
    p.set_defaults(fn=_cmd_demo)

    p = sub.add_parser("ui",
                       help="launch the Streamlit UI "
                            "(requires perforata[app])")
    p.set_defaults(fn=_cmd_ui)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ImportError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
