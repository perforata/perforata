"""End-to-end tests of the Streamlit app using streamlit.testing.AppTest.

These run the real app script — including the startup pipeline load and
the preset hydration path — so a preset that breaks or freezes the UI
fails here.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from perforata import presets

TIMEOUT = 30
APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _run_app() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=TIMEOUT)
    at.run()
    return at


def test_app_starts_without_errors():
    at = _run_app()
    assert not at.exception
    # Default pipeline is selected and named in the sidebar
    assert at.session_state["pipeline_name"] == "honeycomb-vent"


def test_app_not_dirty_after_startup():
    """Freshly loaded pipeline must not immediately show as modified."""
    at = _run_app()
    header = at.sidebar.markdown[0].value
    assert "modified" not in header


def test_editing_a_widget_marks_dirty():
    at = _run_app()
    # Nudge the hex pitch
    at.number_input(key="gen_p").set_value(12.0).run()
    assert not at.exception
    header = at.sidebar.markdown[0].value
    assert "modified" in header


@pytest.mark.parametrize("name", presets.list_factory())
def test_every_factory_preset_renders(name):
    """Load each factory preset through the same hydration path the UI
    uses and confirm the app renders it without exceptions and produces
    cutouts."""
    at = AppTest.from_file(APP_PATH, default_timeout=TIMEOUT)
    payload = presets.load_factory(name)
    at.session_state["_pending_state"] = dict(payload["ui_state"])
    at.session_state["_pending_name"] = name
    at.run()
    assert not at.exception, f"preset {name!r} raised in the app"
    assert at.session_state["pipeline_name"] == name
    # The Cutouts metric must be > 0 (a frozen/empty preview regression
    # would show 0 or hang before this point).
    cutouts = next(m for m in at.metric if m.label == "Cutouts")
    assert int(cutouts.value) > 0, f"preset {name!r} produced no cutouts"
    # Not dirty right after load
    header = at.sidebar.markdown[0].value
    assert "modified" not in header, f"preset {name!r} loads dirty"


def test_advanced_mode_renders():
    at = _run_app()
    at.radio(key="mode_radio").set_value("Advanced").run()
    assert not at.exception


def test_demo_gallery_button():
    """The demo button renders the factory-preset matrix without errors
    and doesn't mark the current pipeline dirty."""
    at = _run_app()
    demo_btn = next(b for b in at.sidebar.button
                    if "Demo gallery" in b.label)
    demo_btn.click().run()
    assert not at.exception
    assert at.session_state["show_demo"] is True
    header = at.sidebar.markdown[0].value
    assert "modified" not in header  # demo view is not a pipeline edit
    # Close it again
    at.button(key="demo_close").click().run()
    assert not at.exception
    assert at.session_state["show_demo"] is False
