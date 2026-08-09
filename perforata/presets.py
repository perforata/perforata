"""Preset storage: save / load / share pipelines with their parameters.

Two tiers:

* **Factory presets** — curated pipelines shipped with the repo. They are
  defined *as code* in :mod:`perforata.factory_presets` (tracked in git,
  reviewable in diffs, immune to pickle format drift) and registered via
  :func:`register_factory`.
* **User presets** — saved from the UI into ``presets/user/`` (excluded
  from git) as ``.pfp`` files serialized with **cloudpickle**, which
  (unlike plain pickle or JSON) handles everything our nodes contain:

  - nested ``tag_rule`` closures inside :class:`~perforata.generators.CartesianGrid`
  - compiled code objects inside :class:`~perforata.fields.Expression`
  - numpy image arrays inside :class:`~perforata.fields.ImageField`
  - arbitrary composed field expressions (``0.3 + 0.7 * ImageField(...)``)

``.pfp`` files can also be downloaded/imported through the UI to share
pipelines between machines.

.. warning::
    Pickle-based formats execute code on load. Only load ``.pfp`` files
    from sources you trust — treat them like Python scripts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import __version__

PRESET_DIR = Path("presets") / "user"
PRESET_EXT = ".pfp"

# Bumped when the payload layout changes incompatibly.
FORMAT_VERSION = 1


def _envelope(payload) -> dict:
    return {
        "format": "perforata-preset",
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "payload": payload,
    }


def dumps(payload) -> bytes:
    """Serialize a payload (any picklable structure of nodes/params)
    to bytes — e.g. for a UI download/share button."""
    import cloudpickle
    return cloudpickle.dumps(_envelope(payload))


def loads(data: bytes):
    """Deserialize preset bytes back into the stored payload."""
    import pickle
    envelope = pickle.loads(data)  # noqa: S301 — documented trust model
    if not isinstance(envelope, dict) or \
            envelope.get("format") != "perforata-preset":
        raise ValueError("not a perforata preset file")
    if envelope.get("format_version", 0) > FORMAT_VERSION:
        raise ValueError(
            f"preset was saved by a newer version "
            f"(format {envelope['format_version']} > {FORMAT_VERSION})")
    return envelope["payload"]


def _path_for(name: str, directory: Path | str | None = None) -> Path:
    directory = Path(directory) if directory else PRESET_DIR
    name = name if name.endswith(PRESET_EXT) else name + PRESET_EXT
    return directory / name


def save(name: str, payload, directory: Path | str | None = None) -> Path:
    """Save a payload under ``presets/<name>.pfp``; returns the path."""
    path = _path_for(name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(payload))
    return path


def load(name: str, directory: Path | str | None = None):
    """Load a preset by name (or full filename) from the presets folder."""
    return loads(_path_for(name, directory).read_bytes())


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
