"""Thin shim so `streamlit run app.py` keeps working from a repo
checkout. The real app lives in the package (src/perforata/app.py) so
the installed wheel can launch it via `perforata ui`."""

from pathlib import Path
import runpy
import sys

# Ensure the src-layout package is importable when running from a
# checkout without an editable install.
_src = Path(__file__).parent / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

runpy.run_module("perforata.app", run_name="__main__")
