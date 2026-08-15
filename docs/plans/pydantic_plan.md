# Plan: adopt pydantic for the params schema (`perforata.api`)

Goal: replace the hand-rolled dict handling in `perforata.api` with typed
**pydantic v2 models** so the pipeline-params contract (`SCHEMA_VERSION`)
becomes self-documenting, strictly validated, and exportable as JSON Schema
for downstream consumers (the perforataio TypeScript frontend, the CLI's
`validate` subcommand, and any future FastAPI backend — which speaks
pydantic natively).

## Why pydantic (and why now)

- The params dict is the package's real public API. Today invalid input
  fails deep inside a node constructor with a numpy/`KeyError` traceback;
  pydantic turns that into precise, path-addressed error messages
  (`modifiers.2.params.field.type: unexpected value`).
- **JSON Schema generation for free** (`model_json_schema()`): perforataio
  can codegen TypeScript types from it (`json-schema-to-typescript`), making
  the browser UI and the engine share one source of truth instead of a
  hand-maintained `PipelineDef` interface.
- **Discriminated unions** model the node system exactly: `generator.type`,
  `modifiers[].type`, and `field.type` are natural discriminators.
- FastAPI (the platform backend) validates request bodies with these same
  models, unchanged.
- Pyodide ships `pydantic` + `pydantic-core` as built packages, so the
  browser worker can keep installing `perforata` with pydantic as a core
  dependency (verify the pinned Pyodide version carries a compatible
  pydantic v2 before release).

## Dependency decision

Make pydantic a **core dependency** (alongside numpy). Rationale: the API
contract *is* the product boundary; hiding validation behind an extra makes
the default install worse. Cost is acceptable: pure wheel + pydantic-core
binary exist for all supported CPython versions and Pyodide.

If we later want a zero-pydantic profile (unlikely), `perforata.api` is the
only importer, so the engine tier stays untouched.

## Model design

New module `src/perforata/schema.py` (models only, no engine imports):

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")   # catch typos loudly

# --- fields (discriminated by "type") --------------------------------
class RadialGradientDef(StrictModel):
    type: Literal["RadialGradient"]
    invert: bool = False
    fscale: float = 1.0

class ShapeGradientDef(StrictModel):
    type: Literal["ShapeGradient"]
    shape: Literal["circle", "square", "rectangle", "hexagon"] = "circle"
    falloff: Literal["exponential", "linear", "quadratic",
                     "gaussian", "sphere"] = "exponential"
    k: float = 3.0
    invert: bool = False
    width: float = 1.0
    height: float = 1.0
    fscale: float = 1.0

# ... LinearGradientDef, ExpressionDef, (later: TextFieldDef, ImageFieldDef)

FieldDef = Annotated[Union[RadialGradientDef, ShapeGradientDef, ...],
                     Field(discriminator="type")]

# --- generators / modifiers: same pattern -----------------------------
GeneratorDef = Annotated[Union[CartesianGridDef, HexGridDef,
                               ConcentricRingsDef],
                         Field(discriminator="type")]
ModifierDef = Annotated[Union[RotateDef, ScaleDef, ..., FieldModulateDef,
                              DensityWarpDef, AttributeFilterDef,
                              TagFilterDef],
                        Field(discriminator="type")]

class ShapeRule(StrictModel):
    shape: Literal["circle", "triangle", "square", "pentagon", "hexagon"] = "hexagon"
    fill: float = Field(0.7, gt=0, le=1)
    rotation: float = 0.0
    absolute: bool = False

class Manufacturing(StrictModel):
    min_hole: float = Field(0.0, ge=0)
    min_wall: float = Field(0.0, ge=0)
    target_d: float = Field(0.0, ge=0)
    crop: CropDef | None = None

class PipelineDef(StrictModel):
    version: Literal[1] = 1
    name: str = "untitled"
    generator: GeneratorDef
    modifiers: list[ModifierDef] = []
    rules: dict[str, ShapeRule] = {"*": ShapeRule()}
    manufacturing: Manufacturing = Manufacturing()
```

Design notes:
- **Flatten `params`**: move from `{"type": X, "params": {...}}` to a flat
  `{"type": X, ...}` per node — pydantic's discriminated unions want the
  discriminator at the same level, and it reads better. This is a schema
  change → see migration below.
- **`extra="forbid"`** everywhere: typo'd keys become validation errors
  instead of silently ignored knobs.
- Numeric constraints (`gt/ge/le`) encode the widget ranges the Streamlit UI
  enforces today, so headless/API users get the same guardrails.
- Each `*Def` gets a `build()` method (or a separate registry function)
  returning the engine node, keeping schema ↔ engine mapping in one place
  and `schema.py` importable without numpy for pure validation tooling.

## API surface changes (`perforata.api`)

```python
def evaluate(params: dict | PipelineDef) -> dict:
    pipeline = PipelineDef.model_validate(params)   # raises ValidationError
    ...

def validate(params: dict) -> list[str]:
    # now a thin wrapper: collect e.errors() into human-readable strings
```

- `evaluate_json` / `export_json` use `model_validate_json` (faster, single
  pass) and can serialize results with a `ResultModel` if we want the output
  side typed too (recommended: `EvaluationResult` with `shapes`, `stats`,
  `timings_ms` — fixes the `np.float64` leakage seen in `stats` today via
  field serializers).
- Export the JSON Schema as a build artifact:
  `perforata schema > pipeline.schema.json` (new CLI subcommand), published
  with each release so perforataio codegen pins to the wheel version.

## Schema versioning & migration

- Old shape (`params`-nested, v1-as-shipped-in-0.3.x) → new flat shape is
  breaking: bump `SCHEMA_VERSION` to 2 and release as **0.4.0**.
- Add `perforata.schema.migrate(params: dict) -> dict` handling v1→v2
  mechanically (hoist `params` up one level). `evaluate` auto-migrates old
  documents and logs a deprecation warning; the CLI `validate` reports the
  version it detected.
- perforataio stores designs as JSONB with the `version` field — its loader
  calls the same `migrate`, so saved designs survive engine upgrades.

## Testing

- Round-trip: every factory preset → `PipelineDef.model_validate` →
  `model_dump` → equal.
- Negative table-driven tests: wrong discriminator, out-of-range `fill`,
  extra keys, v1 documents (must auto-migrate).
- Schema snapshot test: `model_json_schema()` committed to the repo; CI
  fails if it changes without a version bump (protects downstream codegen).
- Pyodide smoke (manual or scheduled): `micropip.install` the new wheel in a
  Pyodide runtime and validate one preset — guards the pydantic-core WASM
  compatibility assumption.

## Execution order

1. Add `pydantic>=2.7` to core deps; write `schema.py` models + `build()`
   mapping (no behavior change — `api` still accepts old dicts).
2. Wire `api.evaluate/validate` through the models; add `migrate` for the
   nested→flat change; bump `SCHEMA_VERSION = 2`.
3. Type the output side (`EvaluationResult`) and fix numpy-scalar leakage.
4. CLI: `perforata schema` subcommand; publish `pipeline.schema.json` as a
   release asset in `release.yml`.
5. Tag **v0.4.0**; update perforataio's `PERFORATA_SPEC` pin and codegen
   TypeScript types from the published schema.
