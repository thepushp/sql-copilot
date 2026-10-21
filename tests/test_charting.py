import datetime
import decimal

import pandas as pd

from services.charting import MAX_BAR_ROWS, MAX_CHART_ROWS, chart_data


def test_category_and_number_gives_bar_chart():
    kind, frame = chart_data(pd.DataFrame({"status": ["a", "b", "c"], "n": [1, 2, 3]}))

    assert kind == "bar"
    assert list(frame.columns) == ["n"]
    assert list(frame.index) == ["a", "b", "c"]


def test_dates_give_a_sorted_line_chart():
    dates = [datetime.date(2026, 1, 3), datetime.date(2026, 1, 1), datetime.date(2026, 1, 2)]

    kind, frame = chart_data(pd.DataFrame({"day": dates, "n": [3, 1, 2]}))

    assert kind == "line"
    assert list(frame["n"]) == [1, 2, 3]


def test_decimal_columns_are_treated_as_numbers():
    frame = pd.DataFrame({"k": ["a", "b"], "amount": [decimal.Decimal("1.5"), decimal.Decimal("2")]})

    assert chart_data(frame)[0] == "bar"


def test_id_columns_are_not_plotted():
    assert chart_data(pd.DataFrame({"name": ["a", "b"], "id": [1, 2], "student_id": [3, 4]})) is None


def test_at_most_three_series():
    frame = pd.DataFrame({"k": ["a", "b"], "a": [1, 2], "b": [1, 2], "c": [1, 2], "d": [1, 2]})

    assert list(chart_data(frame)[1].columns) == ["a", "b", "c"]


def test_not_chartable_cases():
    assert chart_data(None) is None
    assert chart_data(pd.DataFrame({"k": ["a"], "n": [1]})) is None
    assert chart_data(pd.DataFrame({"a": ["x", "y"], "b": ["p", "q"]})) is None
    assert chart_data(pd.DataFrame({"n": [1, 2], "m": [3, 4]})) is None
    assert chart_data(pd.DataFrame({"k": ["a", "a"], "n": [1, 2]})) is None
    assert chart_data(pd.DataFrame({"flag": ["a", "b"], "ok": [True, False]})) is None


def test_row_limits():
    many = pd.DataFrame({"k": [f"k{i}" for i in range(MAX_BAR_ROWS + 1)], "n": range(MAX_BAR_ROWS + 1)})
    too_many = pd.DataFrame(
        {"k": [f"k{i}" for i in range(MAX_CHART_ROWS + 1)], "n": range(MAX_CHART_ROWS + 1)}
    )

    assert chart_data(many) is None
    assert chart_data(too_many) is None
