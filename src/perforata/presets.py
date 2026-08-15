"""Preset storage: save / load / share pipelines with their parameters.

Two tiers:

* **Factory presets** — curated pipelines shipped with the package. They
  are defined *as code* in :mod:`perforata.factory_presets` (tracked in
  git, reviewable in diffs) and registered via :func:`register_factory`.
* **User presets** — saved from the UI into ``presets/user/`` (excluded
  from git) as versioned **JSON** files::

      {"format": "perforata-preset", "v": 1,
       "app_version": "0.3.0", "payload": {"ui_state": {...}}}

  JSON presets are safe to share: loading one cannot execute code.

Legacy ``.pfp`` files (cloudpickle) are still *readable* behind a
deprecation shim — see :func:`loads` — but no longer written. Support
will be removed in the next minor version.

.. warning::
    Pickle-based ``.pfp`` files execute code on load. Only load ``.pfp``
    files from sources you trust — treat them like Python scripts.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Callable

from . import __version__

PRESET_DIR = Path("presets") / "user"
PRESET_EXT = ".json"
LEGACY_EXT = ".pfp"

# Bumped when the payload layout changes incompatibly.
FORMAT_VERSION = 1


def _envelope(payload) -> dict:
    return {
        "format": "perforata-preset",
        "v": FORMAT_VERSION,
        "app_version": __version__,
        "payload": payload,
    }


def _check_envelope(envelope) -> dict:
    if not isinstance(envelope, dict) or \
            envelope.get("format") != "perforata-preset":
        raise ValueError("not a perforata preset file")
    # Legacy pickled envelopes used "format_version"; JSON uses "v".
    saved = envelope.get("v", envelope.get("format_version", 0))
    if saved > FORMAT_VERSION:
        raise ValueError(
            f"preset was saved by a newer version "
            f"(format {saved} > {FORMAT_VERSION})")
    return envelope["payload"]


def dumps(payload) -> bytes:
    """Serialize a payload (a JSON-safe structure, e.g. a UI-state dict)
    to versioned preset JSON bytes — e.g. for a UI download/share
    button."""
    return json.dumps(_envelope(payload), indent=2, sort_keys=True,
                      default=repr).encode("utf-8")


def loads(data: bytes):
    """Deserialize preset bytes back into the stored payload.

    Accepts current JSON presets, plus legacy pickled ``.pfp`` bytes
    behind a :class:`DeprecationWarning` (to be removed in the next
    minor version).
    """
    head = data.lstrip()[:1]
    if head in (b"{", b"["):
        return _check_envelope(json.loads(data.decode("utf-8")))
    # Legacy cloudpickle format
    warnings.warn(
        "pickle-based .pfp presets are deprecated; re-save this preset "
        "to convert it to JSON. .pfp support will be removed in the "
        "next minor version.",
        DeprecationWarning, stacklevel=2)
    import pickle
    envelope = pickle.loads(data)  # noqa: S301 — documented trust model
    return _check_envelope(envelope)


def _path_for(name: str, directory: Path | str | None = None) -> Path:
    directory = Path(directory) if directory else PRESET_DIR
    base_dir = directory.resolve()
    candidate_name = Path(name)

    if candidate_name.is_absolute():
        raise ValueError("preset name must be a relative file name")

    if not name.endswith((PRESET_EXT, LEGACY_EXT)):
        name = name + PRESET_EXT

    path = (directory / name).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError("preset path escapes preset directory") from exc
    return path


def save(name: str, payload, directory: Path | str | None = None) -> Path:
    """Save a payload under ``presets/<name>.json``; returns the path."""
    path = _path_for(name, directory)
    if path.suffix == LEGACY_EXT:
        path = path.with_suffix(PRESET_EXT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(payload))
    return path


def load(name: str, directory: Path | str | None = None):
    """Load a preset by name (or full filename) from the presets folder.

    Falls back to a legacy ``.pfp`` file of the same name if no JSON
    preset exists.
    """
    path = _path_for(name, directory)
    if not path.exists() and path.suffix == PRESET_EXT:
        legacy = path.with_suffix(LEGACY_EXT)
        if legacy.exists():
            path = legacy
    return loads(path.read_bytes())


def list_presets(directory: Path | str | None = None) -> list[str]:
    """Names (without extension) of all stored presets, sorted. Includes
    legacy ``.pfp`` presets that have no JSON counterpart."""
    directory = Path(directory) if directory else PRESET_DIR
    if not directory.is_dir():
        return []
    names = {p.stem for p in directory.glob(f"*{PRESET_EXT}")}
    names |= {p.stem for p in directory.glob(f"*{LEGACY_EXT}")}
    return sorted(names)


def delete(name: str, directory: Path | str | None = None) -> bool:
    """Delete a stored preset (JSON and/or legacy); returns True if any
    file existed."""
    path = _path_for(name, directory)
    deleted = False
    for candidate in {path, path.with_suffix(PRESET_EXT),
                      path.with_suffix(LEGACY_EXT)}:
        if candidate.exists():
            candidate.unlink()
            deleted = True
    return deleted


# ----------------------------------------------------------------------
# Factory presets (tracked in git as code)
# ----------------------------------------------------------------------

_FACTORY: dict[str, Callable[[], dict]] = {}


def register_factory(name: str):
    """Decorator: register a zero-arg function that builds a preset
    payload. Used in :mod:`perforata.factory_presets`."""
    def _wrap(fn: Callable[[], dict]):
        _FACTORY[name] = fn
        return fn
    return _wrap


def list_factory() -> list[str]:
    """Names of all factory presets, sorted."""
    _ensure_factory_loaded()
    return sorted(_FACTORY)


def load_factory(name: str) -> dict:
    """Build a factory preset payload by name."""
    _ensure_factory_loaded()
    return _FACTORY[name]()


def _ensure_factory_loaded():
    # Import registers the presets via the decorator; deferred to avoid
    # a circular import at package load time.
    from . import factory_presets  # noqa: F401
