# perforata — Parametric Perforation Pattern Generator

Generate manufacturable cutout patterns (fan grills, vent panels, speaker
covers, decorative screens) as DXF/SVG files — driven by a composable
node-graph engine with an interactive Streamlit UI.

![Demo gallery of all factory presets](docs/demo_gallery.png)

The matrix above renders every factory preset; view it live via the
**🎬 Demo gallery** button in the UI sidebar, or regenerate the image with
`uv run python -m perforata.demo docs/demo_gallery.png`.

> The original single-file CLI version lives in [`mvp/`](mvp/) and still
> works standalone. This is its structured successor.

## Architecture

Patterns are built as a **graph of small, well-posed processing nodes**
(think Blender Geometry Nodes / Grasshopper, in miniature):

```
Generators ──▶ Modifiers ──▶ Decorators ──▶ Ops ──▶ Exporters
 (centers)     (transform)    (cut shapes)   (crop,  (DXF/SVG)
                   ▲                          filters)
                   │
                Fields (images, gradients, expressions)
```

* **Generators** (`perforata.generators`) produce a `PointCloud` of grid
  centers with attributes (`angle`, `size`, `tag`, lattice coords). The
  theoretical basis is a 2D **Bravais lattice**: points are integer
  combinations of two basis vectors, optionally decorated with a
  multi-point unit cell. Included: `CartesianGrid` (with row/column
  `stagger` for brickwork layouts), `HexGrid`, `ConcentricRings` (the
  MVP's radial system), and the general `Lattice`. Cartesian/hex grids
  accept `fit=True` to snap the pitch so a whole number of cells spans
  the panel and edge margins come out balanced on all sides.
* **Fields** (`perforata.fields`) are scalar functions over the unit
  square: `ImageField` (sample a logo / letter), `TextField`,
  `LinearGradient`, `RadialGradient`, `ShapeGradient`, `Expression`.
  Fields compose with `+ - *` operators.
* **Modifiers** (`perforata.modifiers`) transform point clouds:
  `Affine` (rotation / scaling / shear as proper 2×2 matrix ops that keep
  per-point orientations and sizes consistent), `PolarWarp` (bend a
  cartesian grid into a circle), `FieldModulate` — the "convolve an
  image with the grid" mechanism that drives any point attribute from a
  sampled field — and `DensityWarp`, which remaps grid spacing so point
  density follows a field. Field-sampling modifiers accept
  `region="symmetric"` to map the field over an origin-centered box, so
  radial patterns whose bounding box is slightly lopsided keep the field
  centered on the true pattern center.
* **Decorators** (`perforata.decorators`) instance actual cutout geometry
  onto points. `ShapeInstancer` maps **tags → shape recipes**, so rows
  tagged `even`/`odd` can get up/down triangles, and `major`/`minor` rows
  can get different shapes entirely. This separation of *where centers
  are* from *what is cut there* is the core design change from the MVP.
* **Ops** (`perforata.ops`) handle manufacturability: `Crop` (true
  shapely boolean intersection for flush panel edges — or cull/center
  modes), `MinHoleFilter`, `MinWall`, `FitToSize`.
* **Exporters** (`perforata.exporters`) write DXF (`ezdxf`) or SVG, to a
  path or to bytes (for UI download buttons).

## Install

The core engine depends only on **numpy** and **pydantic** (the typed
params schema); heavier layers are extras:

| Extra | Adds | For |
|---|---|---|
| *(none)* | — | core engine (numpy + pydantic) |
| `geo` | shapely | crop slicing, edge clearance |
| `dxf` | ezdxf | DXF export |
| `raster` | pillow | text/image fields |
| `render` | matplotlib | previews, demo gallery |
| `app` | streamlit (+ all above) | interactive UI |
| `all` | everything | — |

### With pip

```bash
pip install perforata              # core engine
pip install "perforata[dxf]"       # pick the extras you need
pip install "perforata[all]"       # everything
```

### With uv

As a project dependency:

```bash
uv add perforata                   # core engine
uv add "perforata[dxf,render]"     # with extras
```

As a standalone CLI tool (no project needed — uv manages the venv):

```bash
uv tool install "perforata[all]"   # puts `perforata` on your PATH
# or run one-off without installing anything:
uvx --from "perforata[render]" perforata demo -o gallery.png
uvx --from "perforata[all]" perforata ui
```

## Quick start

```bash
# Unified CLI
perforata --version
perforata presets list                      # factory presets
perforata presets show honeycomb-vent
perforata validate pipeline.json            # schema-check a params file
perforata render pipeline.json -o out.svg   # params JSON -> SVG/DXF
perforata schema                            # params JSON Schema -> stdout
perforata demo -o gallery.png               # preset matrix  [render]
perforata ui                                # Streamlit UI   [app]
```

A pipeline params file is plain JSON (the same contract the web
platform uses — see `perforata.api`), validated against typed pydantic
models in `perforata.schema`:

```json
{
  "version": 2,
  "generator": {"type": "HexGrid", "pitch": 9.0,
                "width": 250, "height": 180},
  "rules": {"*": {"shape": "hexagon", "fill": 0.8}},
  "manufacturing": {"min_wall": 1.5}
}
```

`perforata schema` prints the corresponding JSON Schema (published with
each release as `pipeline.schema.json`) for editor autocompletion and
TypeScript codegen. Legacy v1 documents (node params nested under a
`"params"` key) are migrated automatically and can be converted with
`perforata.schema.migrate()`.

Developing from a checkout (requires [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --all-extras          # install everything into .venv
uv run perforata --version    # the CLI, from the checkout
uv run streamlit run app.py   # interactive UI
uv run pytest                 # test suite
uv build                      # sdist + wheel into dist/
```

## Presets

Pipelines can be stored, reloaded and shared:

* **Factory presets** — curated pipelines shipped with the package,
  defined as code in `perforata/factory_presets.py` (tracked in git).
  Load them from the "Factory presets" section in the UI sidebar or via
  `perforata presets list|show`.
* **User presets** — save the current UI pipeline into `presets/user/`
  (git-ignored) as a versioned **JSON** file. The "Share" button
  downloads the same JSON for sending to someone else, who can import it
  via the file uploader. JSON presets cannot execute code on load, so
  they are safe to share.

> Legacy `.pfp` presets (cloudpickle, pre-1.0) are **not supported** —
> `presets.loads()` rejects anything that isn't JSON, and the uploader
> only accepts `.json`. Unpickling arbitrary bytes is arbitrary code
> execution, so there is no read shim and no opt-in. If you still have
> `.pfp` files, open them with a pre-1.0 install of perforata (which had
> `cloudpickle` as a dependency) and re-save them from the UI to convert
> to JSON before upgrading.

The **🎬 Demo gallery** sidebar button renders all factory presets into a
single matrix image (the successor of the MVP's `--demo` grid), with a
download button for the PNG. The same renderer runs headlessly through
`perforata.demo` — no Streamlit needed.

## Using the library

```python
from perforata.generators import CartesianGrid
from perforata.modifiers import FieldModulate
from perforata.fields import ImageField
from perforata.decorators import ShapeInstancer, ShapeSpec
from perforata.ops import Boundary, Crop
from perforata.exporters import DXFExporter
from perforata.graph import Pipeline

# Triangles alternating orientation row by row, sized by a logo image
Pipeline(
    CartesianGrid(pitch_x=8, pitch_y=8, width=300, height=200,
                  alternate=True),
    FieldModulate("scale", ImageField("logo.png", invert=True),
                  lo=0.15, hi=1.0),
    ShapeInstancer(rules={
        "even": ShapeSpec("triangle", fill=0.45, rotation=90),
        "odd":  ShapeSpec("triangle", fill=0.45, rotation=-90),
    }),
    Crop(Boundary.rect(300, 200), mode="cull", include_outline=True),
    DXFExporter("panel.dxf"),
).run()
```

For non-linear compositions (shared upstream nodes, multiple generators
merged), use `perforata.graph.Graph`:

```python
from perforata.graph import Graph
from perforata.modifiers import TagFilter, Merge

g = Graph()
g.add("grid", CartesianGrid(pitch_x=10, pitch_y=10, width=100,
                            height=100, alternate=True))
g.add("even", TagFilter("even"), "grid")
g.add("odd", TagFilter("odd"), "grid")
g.add("merged", Merge(), "even", "odd")
g.add("cuts", ShapeInstancer(shape="circle", fill=0.5), "merged")
shapes = g.run("cuts")
```

## Notes

* Units are dimensionless; treat them as millimeters in CAM. `FitToSize`
  and `Boundary` define true physical dimensions.
* Hole `size` is always the **inscribed diameter** (narrowest opening) —
  the measurement that matters for airflow and minimum-feature checks.
* `ShapeSpec.fill` scales that inscribed diameter relative to the local
  grid pitch, for every shape: at `fill=1.0` neighboring cutouts touch,
  regardless of whether they are circles, hexagons or triangles. Keep it
  below 1.0 to leave walls between holes.
* `Crop(mode="slice")` boolean-cuts straddling shapes flush with the
  panel edge; `mode="cull"` keeps only whole cutouts (usually best for
  perforation panels, since edge slivers can be unmanufacturable).
* The UI's Export section includes a **Config dump (.txt)** button: a
  plain-text report of the widget state and the constructed node
  pipeline (plus result stats), for debugging and bug reports.

## Contributing

Contributions follow a standard feature-branch / pull-request workflow
(direct pushes to `master` are reserved for maintainers):

1. **Branch** off the latest `master`:

   ```bash
   git checkout master && git pull
   git checkout -b feature/my-change     # or fix/..., docs/...
   ```

2. **Make your changes**, with tests. Before committing, verify
   locally what CI will check:

   ```bash
   uv sync --all-extras
   uv run pytest              # test suite
   uv run ruff check .        # lint
   ```

   If you changed the params schema models, regenerate the committed
   snapshot (`uv run perforata schema -o
   tests/data/pipeline.schema.json`) and bump `SCHEMA_VERSION` if the
   change is breaking. Add a note under `## [Unreleased]` in
   `CHANGELOG.md` for anything user-visible.

3. **Push the branch and open a pull request** against `master`:

   ```bash
   git push -u origin feature/my-change
   ```

   CI (`ci.yml`) runs the test matrix and lint on every PR; all checks
   must pass before merge. State your agreement with the
   [CLA](CLA.md) in your first PR.

### Releasing (maintainers)

Releases are tag-driven and gated by `release.yml`:

1. On `master`, bump `[project].version` in `pyproject.toml` and move
   the `## [Unreleased]` notes into a new `## [x.y.z]` section in
   `CHANGELOG.md` (pre-1.0: breaking changes bump the minor version).
2. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The workflow verifies tag ↔ `pyproject.toml` ↔ CHANGELOG agreement
   and that the version isn't already on PyPI, re-runs CI, builds the
   sdist/wheel, publishes to PyPI via Trusted Publishing, and creates a
   GitHub Release with the changelog notes, the dist files, and
   `pipeline.schema.json`.

## License

perforata is licensed under the **GNU Affero General Public License
v3.0 or later** ([LICENSE](LICENSE)). You are free to use, modify, and
share it; if you distribute a modified version or offer it as a network
service, you must make your source available under the same terms. For
commercial licensing outside the AGPL (e.g. embedding the engine in a
proprietary CAD plugin), contact the author.

Contributions are welcome under the project's contributor license
agreement ([CLA.md](CLA.md)): you keep ownership of your work and it
always stays available under the open-source license, while granting
the project the rights needed to also offer commercial licenses. State
your agreement in your first pull request.
