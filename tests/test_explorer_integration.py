"""Runs the Explorer's SQL and exports against a real PostgreSQL.

Opt in with SQL_COPILOT_PG_TEST=1. Everything here is read-only.
"""

import io
import json
import os

import pandas as pd
import pytest

from config.datasource_config import DATASOURCES
from config.privacy import PrivacySettings, apply_privacy
from database.database_inspector import inspect_database
from database.dialects import is_configured
from database.query_executor import execute_query
from database.sql_guard import validate_sql
from services.er_diagram import table_index
from services.exports import build_exports
from services.sql_builders import count_query, page_query, parse_filter, table_key
from services.sql_templates import generate_templates

pytestmark = pytest.mark.integration

MANY = 12
SMALL_TABLE = 200_000


@pytest.fixture(scope="module")
def world():
    if not os.getenv("SQL_COPILOT_PG_TEST"):
        pytest.skip("set SQL_COPILOT_PG_TEST=1 to run PostgreSQL tests")

    name = next((n for n, c in DATASOURCES.items() if is_configured(c)), None)

    if not name:
        pytest.skip("no configured PostgreSQL datasource")

    metadata, policy = apply_privacy(inspect_database(name), PrivacySettings())

    return name, table_index(metadata), policy


def run(world, sql, rows=100):
    name, _, policy = world

    assert validate_sql(sql, policy, "postgresql").allowed, sql

    result = execute_query(name, sql, max_rows=rows)

    assert result.error is None, f"{result.error}\n{sql}"

    return result.rows


def small_tables(world):
    _, index, _ = world

    return [
        t for t in index.values()
        if t.columns and (t.approx_rows or 0) < SMALL_TABLE
    ][:MANY]


def test_first_and_second_page_of_many_tables(world):
    for table in small_tables(world):
        first = run(world, page_query(table, "postgresql", limit=5, offset=0))
        second = run(world, page_query(table, "postgresql", limit=5, offset=5))

        assert len(first) <= 5 and len(second) <= 5, table_key(table)

        keys = [c.name for c in table.columns if c.primary_key]

        if len(keys) == 1 and len(first) and len(second):
            assert not set(first[keys[0]]) & set(second[keys[0]]), "pages overlap"


def test_primary_key_order_is_really_ascending(world):
    for table in small_tables(world):
        keys = [c.name for c in table.columns if c.primary_key]

        if len(keys) != 1:
            continue

        values = run(world, page_query(table, "postgresql", limit=50))[keys[0]].tolist()

        assert values == sorted(values), table_key(table)


def test_descending_sort_starts_with_the_maximum(world):
    _, index, _ = world
    table = index["public.student"] if "public.student" in index else small_tables(world)[0]
    key = next(c.name for c in table.columns if c.primary_key)

    top = run(world, page_query(table, "postgresql", order_by=[(key, True)], limit=1))[key][0]
    biggest = run(world, f'SELECT MAX("{key}") AS m FROM "{table.schema}"."{table.name}"')["m"][0]

    assert top == biggest


def test_filters_on_real_columns(world):
    for table in small_tables(world)[:6]:
        key = next((c.name for c in table.columns if c.primary_key), table.columns[0].name)
        where = parse_filter(f'"{key}" IS NOT NULL', "postgresql")

        rows = run(world, page_query(table, "postgresql", where=where, limit=3))
        total = run(world, count_query(table, "postgresql", where))["total"][0]

        assert len(rows) <= 3 and total >= len(rows)


def test_every_runnable_template_executes(world):
    _, index, _ = world

    for table in small_tables(world):
        for template in generate_templates(table, index, "postgresql"):
            if template.runnable:
                run(world, template.sql, rows=100)


def test_write_templates_are_never_runnable(world):
    _, index, _ = world

    for table in small_tables(world):
        for template in generate_templates(table, index, "postgresql"):
            if template.sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")):
                assert not template.runnable


def test_exports_cope_with_real_column_types(world):
    """Decimals, timestamps with time zones, booleans, arrays, JSON, NULLs."""

    tried = 0

    for table in small_tables(world):
        frame = run(world, page_query(table, "postgresql", limit=500), rows=500)

        if frame.empty:
            continue

        files = {label: data for label, _, _, data in build_exports(frame, table.name)}

        assert len(json.loads(files["JSON"])) == len(frame)
        assert len(pd.read_csv(io.BytesIO(files["CSV"]))) == len(frame)
        assert len(pd.read_excel(io.BytesIO(files["Excel"]))) == len(frame)

        tried += 1

    assert tried >= 3


def test_views_and_materialized_views_can_be_browsed(world):
    _, index, _ = world
    views = [t for t in index.values() if t.kind != "table" and (t.approx_rows or 0) < SMALL_TABLE][:4]

    for view in views:
        run(world, page_query(view, "postgresql", limit=5))
