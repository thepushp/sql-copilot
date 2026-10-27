import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from services.column_profile import (
    SAMPLE_ROWS,
    is_sampled,
    kind_of,
    profile_queries,
    spread_frame,
    summarize,
)


def column(name, data_type):
    return SimpleNamespace(name=name, data_type=data_type)


def table(approx_rows=None):
    return SimpleNamespace(name="sales", schema="public", approx_rows=approx_rows)


def test_kinds():
    assert kind_of(column("a", "bigint")) == "numeric"
    assert kind_of(column("a", "numeric(10,2)")) == "numeric"
    assert kind_of(column("a", "timestamp with time zone")) == "temporal"
    assert kind_of(column("a", "character varying")) == "other"
    assert kind_of(column("a", "interval")) == "other"


def test_queries_for_each_dialect_and_type():
    for dialect in ("postgresql", "mysql", "mssql", "sqlite", "oracle"):
        queries = profile_queries(table(), column("amount", "integer"), dialect)
        assert set(queries) == {"stats", "top", "spread"}
        assert "COUNT(DISTINCT" in queries["stats"].upper()


def test_text_columns_have_no_spread_and_booleans_no_range():
    assert "spread" not in profile_queries(table(), column("name", "text"), "postgresql")

    stats = profile_queries(table(), column("flag", "boolean"), "postgresql")["stats"]
    assert "MIN(" not in stats.upper() and "COUNT(DISTINCT" in stats.upper()


def test_json_columns_skip_distinct_and_top():
    queries = profile_queries(table(), column("doc", "json"), "postgresql")
    assert set(queries) == {"stats"}
    assert "DISTINCT" not in queries["stats"].upper()


def test_big_tables_are_sampled():
    assert is_sampled(table(5_000_000)) and not is_sampled(table(1000)) and not is_sampled(table(None))

    sql = profile_queries(table(5_000_000), column("amount", "integer"), "postgresql")["stats"]
    assert str(SAMPLE_ROWS) in sql and "sample" in sql


def test_queries_return_the_right_answers_on_sqlite(tmp_path):
    path = tmp_path / "p.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sales(region TEXT, amount INTEGER)")
    con.executemany(
        "INSERT INTO sales VALUES (?, ?)",
        [("a", 1), ("a", 2), ("b", 3), (None, None)],
    )
    con.commit()

    spec = SimpleNamespace(name="sales", schema="main", approx_rows=None)
    queries = profile_queries(spec, column("region", "TEXT"), "sqlite")

    stats = pd.read_sql(queries["stats"], con)
    top = pd.read_sql(queries["top"], con)

    summary = summarize(stats)
    assert summary["rows"] == 4 and summary["nulls"] == 1
    assert summary["distinct"] == 2 and summary["min"] == "a" and summary["max"] == "b"
    assert top.iloc[0].tolist() == ["a", 2]
    assert summary["null_share"] == pytest.approx(0.25)

    spread = pd.read_sql(
        profile_queries(spec, column("amount", "INTEGER"), "sqlite")["spread"], con
    )
    assert sorted(spread["value"]) == [1, 2, 3]


def test_summarize_empty():
    assert summarize(None) == {} and summarize(pd.DataFrame()) == {}


def test_spread_frame_drops_bad_cells():
    frame = pd.DataFrame({"value": ["1", "x", "3"]})
    assert list(spread_frame(frame, "numeric")["value"]) == [1, 3]

    dates = pd.DataFrame({"value": ["2024-01-01", "nope"]})
    assert len(spread_frame(dates, "temporal")) == 1
