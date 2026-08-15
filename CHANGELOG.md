# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: breaking changes bump the minor version).

## [Unreleased]

## [0.4.1] - 2026-08-15

### Security
- **Path injection (CWE-22) in user presets**: Fixed an issue where saving, loading, or deleting a user preset by name did not properly validate that the resulting path remained inside the `presets/user/` directory. An attacker could craft names like `../../victim.txt` or absolute paths to read, overwrite, or delete arbitrary files. `presets._path_for` now correctly normalizes paths and enforces containment.

## [0.4.0] - 2026-08-15

### Added
- **Typed params schema** (`perforata.schema`): pydantic v2 models for
  the whole pipeline contract (`PipelineDef`, discriminated unions for
  generators / modifiers / fields, `ShapeRule`, `Manufacturing`).
  Invalid params now fail with precise, path-addressed messages
  (`modifiers.0.field.type: ...`) instead of deep engine tracebacks,
  and typo'd keys are rejected (`extra="forbid"`).
- `perforata schema` CLI subcommand: print (or `-o` write) the params
  JSON Schema for downstream codegen; published as a release asset
  (`pipeline.schema.json`).
- `perforata.schema.migrate()` / `detect_version()`: mechanical
  v1 -> v2 params migration; `evaluate`/`export`/`validate` auto-migrate
  v1 documents (with a `DeprecationWarning`), and `perforata validate`
  reports the detected schema version.
- Schema snapshot test (`tests/data/pipeline.schema.json`): CI fails if
  the schema changes without regenerating the committed snapshot.

### Changed
- **Params schema v2** (`SCHEMA_VERSION = 2`): node definitions are
  flat — `{"type": "HexGrid", "pitch": 9.0}` instead of
  `{"type": "HexGrid", "params": {"pitch": 9.0}}` — and the version key
  is `"version"` (was `"v"`). v1 documents keep working via automatic
  migration.
- **pydantic (>= 2.7) is now a core dependency** alongside numpy: the
  params contract is the package's public API boundary.
- `api.evaluate` stats are now guaranteed plain JSON types (fixed
  potential `np.float64` leakage).

### Deprecated
- Params schema v1 (nested `"params"` keys). Auto-migration keeps v1
  documents working for now; migrate saved documents with
  `perforata.schema.migrate()`.

## [0.3.1] - 2026-08-15

### Fixed
- CI runner warnings: bumped to Node 24-native actions
  (`checkout@v5`, `setup-uv@v7`, `upload-artifact@v5`) and keyed the uv
  dependency cache on `pyproject.toml` (uv.lock is gitignored, so the
  cache never invalidated). CI now also triggers on pushes to `master`.

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
