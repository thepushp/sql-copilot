import pandas as pd

from ui.results import column_config, filter_rows


def frame():
    return pd.DataFrame(
        {
            "name": ["Alice Smith", "Bob Jones", "Carol Smith"],
            "city": ["Paris", "Berlin", "Paris"],
            "n": [1500, 20, 3],
        }
    )


def test_filter_matches_any_column_case_insensitively():
    assert list(filter_rows(frame(), "SMITH")["name"]) == ["Alice Smith", "Carol Smith"]


def test_filter_needs_every_word():
    assert list(filter_rows(frame(), "smith paris")["name"]) == ["Alice Smith", "Carol Smith"]
    assert list(filter_rows(frame(), "bob paris")["name"]) == []


def test_filter_matches_numbers_and_ignores_blank():
    assert list(filter_rows(frame(), "1500")["name"]) == ["Alice Smith"]
    assert len(filter_rows(frame(), "   ")) == 3
    assert len(filter_rows(frame(), None)) == 3


def test_column_config_formats_numbers_and_timestamps_only():
    data = frame()
    data["at"] = pd.to_datetime(["2024-01-01"] * 3)
    data["ok"] = [True, False, True]

    config = column_config(data)

    assert set(config) == {"n", "at"}
