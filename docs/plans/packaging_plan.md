# Plan: perforata as a formal, well-boundaried PyPI package

Goal: restructure the repo so the **engine** is a clean, dependency-minimal
library published to PyPI (`pip install perforata`), with a unified CLI,
optional extras for the heavier layers (UI, rendering), and a GitHub Actions
pipeline that tests, builds, and publishes on tagged releases.

Context: the engine is also consumed by **perforataio** (the web platform),
which runs it unmodified in the browser via Pyodide. That consumer drives the
most important boundary requirement: *the core must import nothing but numpy
(+ shapely for crop ops), with everything else optional.*

---

## 1. Package boundaries (dependency tiers)

Today `pyproject.toml` declares numpy, ezdxf, shapely, matplotlib, pillow,
streamlit, and cloudpickle as hard deps, but the engine already lazy-imports
most of them. Formalize that as extras:

| Tier | Modules | Required deps |
|---|---|---|
| **core** (always) | `pointcloud`, `graph`, `generators`, `fields`*, `modifiers`, `decorators`, `ops`*, `exporters`*, `presets`* | `numpy` |
| **extra: geo** | `ops.Crop(mode="slice")`, `edge_clearance` | `shapely` |
| **extra: dxf** | `exporters.DXFExporter` | `ezdxf` |
| **extra: raster** | `fields.TextField`, `fields.ImageField` | `pillow` |
| **extra: render** | `render`, `demo` | `matplotlib` |
| **extra: app** | `app.py` (Streamlit UI) | `streamlit`, `cloudpickle`, all above |

\* keep lazy imports at function/method level (already mostly done); add
guarded `ImportError` messages telling the user which extra to install,
e.g. `pip install perforata[dxf]`.

```toml
[project]
dependencies = ["numpy"]

[project.optional-dependencies]
geo    = ["shapely"]
dxf    = ["ezdxf"]
raster = ["pillow"]
render = ["matplotlib"]
app    = ["streamlit", "cloudpickle", "perforata[geo,dxf,raster,render]"]
all    = ["perforata[app]"]
```

Decisions to make while doing this:
- **Move `app.py` into the package** (`perforata/app.py` or a `perforata_app`
  sub-package) so `perforata ui` can launch it from an installed wheel —
  today it only works from a repo checkout.
- **Presets format**: cloudpickle `.pfp` files are a code-execution risk for
  a published package and are already superseded by the JSON params schema
  developed in perforataio (`spike/bridge.py`). Migrate presets to
  versioned JSON (`{"v": 1, "ui_state": ...}`); keep `.pfp` reading behind a
  deprecation shim for one minor version.
- **Adopt the params-dict dispatcher** (perforataio's `bridge.py`) into the
  package proper as `perforata.api` — `evaluate(params: dict) -> dict`.
  It becomes the shared contract for the CLI, the web platform, and any
  future server. Single source of truth, tested here.

## 2. Unified CLI

Add `perforata/cli.py` using **argparse** (stdlib — no click/typer dep in
core) exposed as a console script:

```toml
[project.scripts]
perforata = "perforata.cli:main"
```

Subcommands:

```text
perforata render  <pipeline.json> -o out.svg|out.dxf   # params JSON -> file
perforata presets list|show <name>                     # factory presets
perforata demo    -o gallery.png                       # preset matrix [render]
perforata ui                                           # launch Streamlit [app]
perforata validate <pipeline.json>                     # schema-check params
perforata --version
```

Each subcommand imports its tier lazily and fails with a friendly
"install perforata[render]" message when the extra is missing.

## 3. Repo/layout changes

```text
perforata/
├── src/perforata/          # move to src-layout (import hygiene for tests)
│   ├── __init__.py …       # existing engine modules
│   ├── api.py              # params-dict evaluate/export (from perforataio bridge)
│   ├── cli.py               # argparse CLI
│   └── presets/factory/*.json
├── app.py                   # thin shim: `from perforata.app import main`
├── tests/
├── docs/
│   └── plans/packaging_plan.md   # this file
├── pyproject.toml
└── .github/workflows/
    ├── ci.yml               # test on push/PR
    └── release.yml          # build + publish on tag
```

- **src-layout** ensures tests run against the installed wheel, catching
  packaging bugs (missing data files, broken extras) before publish.
- Include factory presets as package data (`[tool.hatch.build]` already uses
  hatchling; add `force-include` or keep them as `.py` registrations).
- Add `__version__` single-sourcing: `version` from
  `importlib.metadata` at runtime, or hatchling's dynamic version from a
  `src/perforata/__about__.py`.

## 4. Versioning & release discipline

- **SemVer.** Params-schema changes are the API surface now: breaking schema
  changes bump minor pre-1.0 (`0.3 -> 0.4`), and the schema carries its own
  `"v"` field so consumers (perforataio) can migrate.
- Git tags `vX.Y.Z` are the release trigger.
- Keep a `CHANGELOG.md` (Keep-a-Changelog format).

## 5. GitHub Actions pipeline

### `ci.yml` — every push / PR
1. `astral-sh/setup-uv` (uv is the project's toolchain).
2. Matrix: python 3.11 / 3.12 / 3.13 on ubuntu (+ one windows job).
3. `uv sync --all-extras && uv run pytest`.
4. Lint: `uv run ruff check .` (add ruff to dev group).
5. Build check: `uv build` then `uv run --with dist/*.whl --no-project python -c "import perforata"` — proves the wheel is importable with core deps only.

### `release.yml` — on tag `v*`
1. Run the full CI job.
2. `uv build` (sdist + wheel).
3. Publish with **PyPI Trusted Publishing** (OIDC — no API token secrets):
   configure the GitHub repo as a trusted publisher on PyPI, then use
   `pypa/gh-action-pypi-publish@release/v1` with `permissions: id-token: write`.
4. Create a GitHub Release with the changelog excerpt and the built
   artifacts attached.
5. (Later) trigger a repository-dispatch to perforataio so the web platform
   can bump its vendored/pinned engine version automatically.

## 6. Consumption by perforataio (the web platform)

Publishing to PyPI makes the Pyodide story cleaner: instead of fetching
loose `.py` files, the web worker installs the real wheel:

```js
await micropip.install("perforata==0.3.0");   // pure-python wheel from PyPI
```

Requirements for that to work (all satisfied by this plan):
- pure-Python wheel (`py3-none-any`) — yes, engine is pure Python;
- core deps limited to Pyodide-available packages (numpy, shapely) — yes,
  provided extras stay optional;
- no import-time matplotlib/streamlit/PIL — enforced by the tier split and
  a CI test that imports every core module with only numpy+shapely present.

## 7. Suggested execution order

1. Extras split + lazy-import guards; move to src-layout. (No behavior change.)
2. Port `bridge.py` from perforataio into `perforata.api` + tests.
3. CLI (`render`, `validate`, `presets`, `--version`), then `demo`/`ui`.
4. JSON presets migration (+ `.pfp` deprecation shim).
5. `ci.yml`; fix anything it finds.
6. PyPI Trusted Publisher setup + `release.yml`; tag `v0.3.0`.
7. Switch perforataio's worker to `micropip.install("perforata")`.
