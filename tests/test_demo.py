"""Tests of the headless demo gallery (perforata.demo)."""

import pytest

from perforata import presets
from perforata.demo import gallery_png, render_gallery, run_state


@pytest.mark.parametrize("name", presets.list_factory())
def test_run_state_matches_ui_for_every_preset(name):
    """Every factory preset must produce cutouts headlessly — a preset
    whose UI-state keys drift from the demo interpreter fails here."""
    cuts = run_state(presets.load_factory(name)["ui_state"])
    assert len(cuts) > 0, f"preset {name!r} produced no cutouts headlessly"


def test_gallery_png_renders_all_presets():
    png = gallery_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature
    assert len(png) > 10_000  # a real image, not an empty canvas


def test_render_gallery_subset_layout():
    import matplotlib.pyplot as plt
    names = presets.list_factory()[:2]
    fig = render_gallery(names, cols=2)
    # One row of two panels, both with content-driven titles
    titles = [ax.get_title() for ax in fig.axes if ax.get_title()]
    assert titles == names
    plt.close(fig)
