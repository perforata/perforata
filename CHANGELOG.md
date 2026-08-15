# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: breaking changes bump the minor version).

## [Unreleased]

## [0.3.0] - 2026-08-15

### Added
- `perforata.api` — params-dict pipeline contract shared by the CLI, the
  web platform (perforataio/Pyodide), and future servers:
  `evaluate(params) -> dict`, `export(params, fmt) -> bytes`,
  `validate(params) -> list[str]`, plus JSON-string wrappers.
- Unified `perforata` console script (stdlib argparse):
  `render`, `validate`, `presets list|show`, `demo`, `ui`, `--version`.
- Optional-dependency extras: `geo` (shapely), `dxf` (ezdxf),
  `raster` (pillow), `render` (matplotlib), `app` (streamlit + all),
  `all`. The core now depends on **numpy only**; missing extras fail
  with an actionable `pip install perforata[<extra>]` message.
- `CHANGELOG.md`, CI (`ci.yml`) and release (`release.yml`) workflows
  with PyPI Trusted Publishing.

### Changed
- **Presets are now versioned JSON** (`{"format": "perforata-preset",
  "v": 1, ...}`) instead of cloudpickle `.pfp`. JSON presets cannot
  execute code on load.
- Repo moved to **src-layout** (`src/perforata/`); the Streamlit app
  moved into the package (`perforata/app.py`) so `perforata ui` works
  from an installed wheel. The repo-root `app.py` is now a thin shim.
- `__version__` is single-sourced from package metadata
  (`importlib.metadata`).

### Deprecated
- Reading legacy pickle-based `.pfp` presets still works but emits a
  `DeprecationWarning`; support will be removed in 0.4.0. Writing `.pfp`
  is no longer possible — presets save as `.json`.

## [0.2.0]
- Node-graph engine, Streamlit UI, factory presets, demo gallery.

## [0.1.0]
- Single-file MVP (`mvp/perforata.py`).
