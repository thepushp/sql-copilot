import datetime
import decimal
import io
import json

import pandas as pd
import pytest

from services.data_profile import profile
from services.exports import EXPORT_ROW_LIMIT, build_exports, to_csv, to_excel, to_json


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["Ann", "Bob", "Zoë"],
            "spent": [decimal.Decimal("1.50"), decimal.Decimal("2"), None],
            "day": [datetime.date(2026, 1, 1), datetime.date(2026, 1, 2), None],
        }
    )


class TestExports:

    def test_csv(self, frame):
        data = to_csv(frame)

        assert data.startswith(b"\xef\xbb\xbf"), "BOM so Excel opens UTF-8 correctly"
        text = data.decode("utf-8-sig")
        assert text.splitlines()[0] == "id,name,spent,day"
        assert "Zoë" in text

    def test_csv_quotes_awkward_values(self):
        text = to_csv(pd.DataFrame({"a": ['say "hi", ok', "line\nbreak"]})).decode("utf-8-sig")

        assert pd.read_csv(io.StringIO(text))["a"].tolist() == ['say "hi", ok', "line\nbreak"]

    def test_json_is_a_list_of_records(self, frame):
        records = json.loads(to_json(frame))

        assert records[0]["name"] == "Ann"
        assert records[2]["name"] == "Zoë"
        assert records[2]["spent"] is None

    def test_json_keeps_unicode_readable(self, frame):
        assert "Zoë" in to_json(frame).decode("utf-8")

    def test_excel_round_trips(self, frame):
        back = pd.read_excel(io.BytesIO(to_excel(frame)), sheet_name="Data")

        assert back["name"].tolist() == ["Ann", "Bob", "Zoë"]
        assert back["id"].tolist() == [1, 2, 3]

    def test_excel_copes_with_time_zones(self):
        frame = pd.DataFrame({"at": pd.to_datetime(["2026-01-01T10:00:00Z"])})

        back = pd.read_excel(io.BytesIO(to_excel(frame)))

        assert str(back["at"][0]).startswith("2026-01-01 10:00")

    def test_empty_results_still_export(self):
        empty = pd.DataFrame({"a": [], "b": []})

        assert to_csv(empty).decode("utf-8-sig").strip() == "a,b"
        assert json.loads(to_json(empty)) == []
        assert pd.read_excel(io.BytesIO(to_excel(empty))).empty

    def test_original_frame_is_not_modified(self):
        frame = pd.DataFrame({"at": pd.to_datetime(["2026-01-01T10:00:00Z"])})

        to_excel(frame)

        assert str(frame["at"].dtype) != "datetime64[ns]"

    def test_build_exports_names_and_types(self, frame):
        files = build_exports(frame, "orders")

        assert [(label, name) for label, name, _, _ in files] == [
            ("CSV", "orders.csv"), ("JSON", "orders.json"), ("Excel", "orders.xlsx"),
        ]
        assert files[0][2] == "text/csv"
        assert all(isinstance(data, bytes) and data for _, _, _, data in files)

    @pytest.mark.parametrize(
        "table,expected",
        [
            ("orders", "orders"),
            ("my table", "my_table"),
            ("a/b\\c", "a_b_c"),
            ("../../etc/passwd", "etc_passwd"),
            ("naïve", "naïve"),
            ("", "export"),
            ("...", "export"),
        ],
    )
    def test_file_names_are_made_safe(self, frame, table, expected):
        names = [name for _, name, _, _ in build_exports(frame, table)]

        assert names == [f"{expected}.csv", f"{expected}.json", f"{expected}.xlsx"]
        assert all("/" not in n and "\\" not in n and ".." not in n for n in names)

    def test_limit_is_a_sensible_number(self):
        assert EXPORT_ROW_LIMIT == 10_000


class TestProfile:

    def test_counts_and_ranges(self, frame):
        stats = profile(frame).set_index("Column")

        assert stats.loc["id", ["Values", "Nulls", "Distinct", "Min", "Max"]].tolist() == [3, 0, 3, "1", "3"]
        assert stats.loc["spent", "Nulls"] == 1
        assert stats.loc["spent", "Values"] == 2
        assert stats.loc["name", "Min"] == "Ann"

    def test_all_null_column(self):
        stats = profile(pd.DataFrame({"a": [None, None]})).iloc[0]

        assert (stats["Values"], stats["Nulls"], stats["Distinct"], stats["Min"]) == (0, 2, 0, "")

    def test_unhashable_and_mixed_values(self):
        frame = pd.DataFrame({"tags": [["a"], ["b"], ["a"]], "mixed": [1, "x", 2.5]})

        stats = profile(frame).set_index("Column")

        assert stats.loc["tags", "Distinct"] == 2
        assert stats.loc["mixed", "Distinct"] == 3

    def test_long_values_are_shortened(self):
        stats = profile(pd.DataFrame({"t": ["x" * 200, "y" * 200]})).iloc[0]

        assert len(stats["Min"]) <= 40 and stats["Min"].endswith("…")

    def test_empty_frame(self):
        assert len(profile(pd.DataFrame({"a": []}))) == 1
        assert profile(pd.DataFrame()).empty


class TestRealDatabaseTypes:
    """Values drivers really return: found by exporting a live PostgreSQL."""

    import ipaddress
    import uuid

    ID = uuid.UUID("e458a418-b0d1-70e9-46af-7441264bed6e")

    @pytest.fixture
    def rich(self):
        return pd.DataFrame(
            {
                "id": [self.ID, None],
                "tags": [["a", "b"], []],
                "doc": [{"x": 1, "nested": {"y": [1, 2]}}, None],
                "tuple": [(1, 2), None],
                "unique": [{"b", "a"}, set()],
                "raw": [b"\xd9\x01", memoryview(b"\x00\xff")],
                "ip": [self.ipaddress.ip_address("10.0.0.1"), None],
                "span": [datetime.timedelta(hours=1, minutes=5), None],
                "money": [decimal.Decimal("12.50"), decimal.Decimal("12345678901234567.89")],
                "whole": [decimal.Decimal("7"), decimal.Decimal("8")],
                "when": [datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc), None],
                "day": [datetime.date(2026, 1, 2), None],
                "clock": [datetime.time(9, 30), None],
                "ok": [True, False],
            }
        )

    def test_json_is_valid_and_readable(self, rich):
        records = json.loads(to_json(rich))

        assert records[0]["id"] == "e458a418-b0d1-70e9-46af-7441264bed6e"
        assert records[0]["tags"] == ["a", "b"]
        assert records[0]["doc"] == {"x": 1, "nested": {"y": [1, 2]}}
        assert records[0]["tuple"] == [1, 2]
        assert records[0]["unique"] == ["a", "b"]
        assert records[0]["raw"] == "d901" and records[1]["raw"] == "00ff"
        assert records[0]["ip"] == "10.0.0.1"
        assert records[0]["span"] == "P0DT1H5M0S", "ISO 8601 duration"
        assert records[0]["money"] == 12.5 and records[0]["whole"] == 7
        assert records[1]["money"] == "12345678901234567.89", "too precise for a float, kept as text"
        assert records[0]["when"].startswith("2026-01-02T03:04:05")
        assert records[0]["day"] == "2026-01-02" and records[0]["clock"] == "09:30:00"
        assert records[1]["id"] is None and records[1]["doc"] is None

    def test_csv_is_valid(self, rich):
        text = to_csv(rich).decode("utf-8-sig")
        back = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)

        assert back["id"][0] == "e458a418-b0d1-70e9-46af-7441264bed6e"
        assert json.loads(back["tags"][0]) == ["a", "b"]
        assert json.loads(back["doc"][0])["nested"] == {"y": [1, 2]}
        assert back["raw"][0] == "d901"
        assert back["ip"][0] == "10.0.0.1"
        assert back["id"][1] == ""

    def test_excel_is_valid(self, rich):
        back = pd.read_excel(io.BytesIO(to_excel(rich)), sheet_name="Data", dtype=str)

        assert back["id"][0] == "e458a418-b0d1-70e9-46af-7441264bed6e"
        assert json.loads(back["tags"][0]) == ["a", "b"]
        assert back["ip"][0] == "10.0.0.1"
        assert back["when"][0].startswith("2026-01-02 03:04:05")

    def test_the_original_frame_is_untouched(self, rich):
        before = rich.copy(deep=True)

        build_exports(rich, "t")

        assert rich["id"][0] == before["id"][0] and isinstance(rich["tags"][0], list)

    def test_pandas_missing_markers(self):
        frame = pd.DataFrame({"a": [pd.NA, float("nan"), None, "x"], "b": [pd.NaT, pd.NaT, pd.NaT, pd.NaT]})

        records = json.loads(to_json(frame))

        assert [r["a"] for r in records] == [None, None, None, "x"]
        assert all(r["b"] is None for r in records)

    def test_numpy_scalars_survive(self):
        import numpy as np

        frame = pd.DataFrame({"n": pd.Series([np.int64(5), np.int64(6)], dtype=object)})

        assert [r["n"] for r in json.loads(to_json(frame))] == [5, 6]


class TestDisplaySafe:

    def test_mixed_list_and_scalar_column_can_become_an_arrow_table(self):
        import pandas as pd
        import pyarrow as pa
        from services.exports import display_safe

        frame = pd.DataFrame({"fees": [[1, 2], 5, None, {"a": 1}], "n": [1, 2, 3, 4]})

        with __import__("pytest").raises(Exception):
            pa.Table.from_pandas(frame)

        safe = display_safe(frame)

        assert pa.Table.from_pandas(safe).num_rows == 4
        assert safe["fees"].tolist()[0] == "[1, 2]"
        assert safe["fees"].tolist()[1] == "5"
        assert pd.isna(safe["fees"].tolist()[2])
