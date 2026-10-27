import sqlite3

import pytest

from services.query_plan import explain
from services.sql_builders import equals_filter, parse_filter


@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mssql", "sqlite", "oracle"])
def test_equals_filter_round_trips_through_parse_filter(dialect):
    for value in (5, 2.5, "O'Brien", "a;DROP TABLE x", True):
        text = equals_filter("my col", value, dialect)
        assert parse_filter(text, dialect) is not None
        assert ";" not in text.replace("a;DROP TABLE x", "")


def test_equals_filter_escapes_quotes():
    assert equals_filter("name", "O'Brien", "postgresql") == "\"name\" = 'O''Brien'"
    assert equals_filter("id", 5, "postgresql") == '"id" = 5'


def test_explain_on_sqlite(tmp_path):
    path = tmp_path / "p.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT); CREATE INDEX i ON t(v);")
    con.close()

    config = {"driver": "sqlite", "database": str(path)}

    text, error = explain("p", "SELECT * FROM t WHERE v = 'x'", config, "sqlite")

    assert error is None
    assert text.startswith("SEARCH t") and "INDEX i" in text


def test_explain_reports_errors_and_unsupported(tmp_path):
    path = tmp_path / "q.db"
    sqlite3.connect(path).close()
    config = {"driver": "sqlite", "database": str(path)}

    assert explain("q", "SELECT * FROM missing", config, "sqlite")[1]
    assert explain("q", "SELECT 1", config, "oracle")[0] is None
    assert explain("q", "SELECT 1", config, "mssql")[0] is None
