# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: breaking changes bump the minor version).

## [Unreleased]

### Added
- **Field offset**: `Field.offset(du, dv)` translates a field in uv
  space (mirrors `Field.scaled`); every field def in the params schema
  now accepts `offset_u`/`offset_v`, applied after `fscale`.
- **`ImageField` in the params schema**: fields of `"type": "ImageField"`
  are now first-class — `luminance_b64` (a base64-encoded uint8
  luminance grid, row-major, `w`×`h`) plus `invert`/`fit`/`bg`/`smooth`,
  validated at schema time (base64 well-formed, payload length matches
  `w * h`). Lets browser-uploaded images flow through `perforata.api`
  without a client-side shim.

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
