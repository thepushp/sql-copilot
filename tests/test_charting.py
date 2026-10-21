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


class TestMakeChart:

    def frame(self):
        import pandas as pd
        return pd.DataFrame({"city": ["a", "a", "b"], "total": [1, 2, 5], "kind": ["x", "y", "x"]})

    def test_aggregates_repeated_labels(self):
        from services.charting import chartable_columns, prepare_chart

        frame = self.frame()
        everything, numeric = chartable_columns(frame)

        assert numeric == ["total"] and "city" in everything
        tidy, x_type = prepare_chart(frame, "city", "total", "Bar", "Sum")
        assert x_type == "nominal" and tidy.set_index("x")["y"].to_dict() == {"a": 3, "b": 5}
        assert len(prepare_chart(frame, "city", "total")[0]) == 3

    def test_split_by_keeps_a_series_column(self):
        from services.charting import prepare_chart

        tidy, _ = prepare_chart(self.frame(), "city", "total", "Bar", "Sum", "kind")
        assert set(tidy["series"]) == {"x", "y"}

    def test_every_kind_builds_a_chart(self):
        from services.charting import CHART_KINDS, make_chart

        for kind in CHART_KINDS:
            x = "total" if kind == "Histogram" else "city"
            assert make_chart(self.frame(), x, "total", kind, "Sum").to_dict()

    def test_dates_are_temporal_and_numbers_are_not_forced_to_text(self):
        import pandas as pd
        from services.charting import prepare_chart

        frame = pd.DataFrame({"day": pd.to_datetime(["2024-01-01", "2024-01-02"]), "n": [1, 2], "v": [3, 4]})
        assert prepare_chart(frame, "day", "v", "Line")[1] == "temporal"
        assert prepare_chart(frame, "n", "v", "Scatter")[1] == "quantitative"
        assert prepare_chart(frame, "n", "v", "Bar")[1] == "nominal"

    def test_many_categories_are_trimmed(self):
        import pandas as pd
        from services.charting import MAX_CATEGORIES, prepare_chart

        frame = pd.DataFrame({"k": [str(i) for i in range(100)], "v": range(100)})
        assert len(prepare_chart(frame, "k", "v", "Bar")[0]) == MAX_CATEGORIES

    def test_rejects_non_numeric_y(self):
        import pandas as pd
        import pytest
        from services.charting import make_chart

        with pytest.raises(ValueError):
            make_chart(pd.DataFrame({"a": ["x"], "b": ["y"]}), "a", "b", "Bar", "Sum")
