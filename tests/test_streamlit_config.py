import tomllib
from pathlib import Path


CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"


def load():
    return tomllib.loads(CONFIG.read_text())


def test_listens_on_this_machine_only():
    assert load()["server"]["address"] == "127.0.0.1"


def test_sends_no_usage_statistics():
    assert load()["browser"]["gatherUsageStats"] is False


def test_keeps_the_three_dot_menu_with_rerun():
    """'minimal' removes the whole menu and 'viewer' removes Rerun from it."""

    assert load()["client"]["toolbarMode"] == "developer"


def test_theme_colour_is_a_valid_hex():
    colour = load()["theme"]["primaryColor"]

    assert len(colour) == 7 and colour.startswith("#")
    int(colour[1:], 16)
