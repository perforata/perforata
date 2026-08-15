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

Legacy ``.pfp`` files (cloudpickle) are no longer supported — :func:`loads`
rejects them outright. Unpickling attacker-controlled bytes is arbitrary
code execution (CWE-502), so there is no shim and no opt-in. Old ``.pfp``
presets must be re-saved as JSON using a pre-1.0 version of perforata
before upgrading.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from . import __version__

PRESET_DIR = Path("presets") / "user"
PRESET_EXT = ".json"

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
    saved = envelope.get("v", 0)
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

    Only versioned JSON presets are accepted. Legacy pickled ``.pfp``
    bytes (and any other non-JSON input) are rejected outright — unlike
    JSON, unpickling arbitrary bytes can execute code, so there is no
    fallback path.
    """
    head = data.lstrip()[:1]
    if head not in (b"{", b"["):
        raise ValueError(
            "not a perforata preset file: legacy pickle-based .pfp "
            "presets are no longer supported. Re-save the preset as "
            "JSON with a pre-1.0 version of perforata, then import the "
            "resulting .json file.")
    return _check_envelope(json.loads(data.decode("utf-8")))


def _path_for(name: str, directory: Path | str | None = None) -> Path:
    directory = Path(directory) if directory else PRESET_DIR

    if Path(name).is_absolute():
        raise ValueError("preset name must be a relative file name")

    if not name.endswith(PRESET_EXT):
        name = name + PRESET_EXT

    # Canonicalize (following symlinks) and require the result to stay
    # inside the preset directory — user-supplied names must not be able
    # to address files elsewhere (CWE-22).
    base_dir = os.path.realpath(directory)
    candidate = os.path.realpath(os.path.join(base_dir, name))
    if not candidate.startswith(base_dir + os.sep):
        raise ValueError("preset path escapes preset directory")
    return Path(candidate)


def save(name: str, payload, directory: Path | str | None = None) -> Path:
    """Save a payload under ``presets/<name>.json``; returns the path."""
    path = _path_for(name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(payload))
    return path


def load(name: str, directory: Path | str | None = None):
    """Load a preset by name (or full filename) from the presets folder."""
    path = _path_for(name, directory)
    return loads(path.read_bytes())


def list_presets(directory: Path | str | None = None) -> list[str]:
    """Names (without extension) of all stored presets, sorted."""
    directory = Path(directory) if directory else PRESET_DIR
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob(f"*{PRESET_EXT}"))


def delete(name: str, directory: Path | str | None = None) -> bool:
    """Delete a stored preset; returns True if it existed."""
    path = _path_for(name, directory)
    if path.exists():
        path.unlink()
        return True
    return False


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
