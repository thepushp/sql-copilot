"""Profile one column: null share, distinct count, range, common values, spread.

Queries are built with sqlglot (so they suit every dialect) and are meant to be
run through the usual safety check. Large tables are profiled on their first
rows instead of a full scan.
"""

import re

import pandas as pd
from sqlglot import exp

from services.sql_builders import column_ref, table_ref, to_sql


SAMPLE_ABOVE_ROWS = 200_000
SAMPLE_ROWS = 100_000
TOP_VALUES = 10
SPREAD_ROWS = 5_000

_NUMERIC = re.compile(
    r"\b(?:(?:tiny|small|medium|big)?int(?:eger)?\d*|(?:small|big)?serial\d*"
    r"|numeric|decimal|float\d*|double|real|number|money)\b",
    re.IGNORECASE,
)
_TEMPORAL = re.compile(r"date|time", re.IGNORECASE)
_NO_RANGE = re.compile(
    r"bool|bit|json|xml|array|\[\]|uuid|inet|cidr|mac|point|polygon|circle|box|path",
    re.IGNORECASE,
)
_NO_DISTINCT = re.compile(r"^json$|^json\b(?!b)|xml|point|polygon|circle|box", re.IGNORECASE)


def kind_of(column):
    """"numeric", "temporal" or "other" from the declared type."""

    text = column.data_type or ""

    if re.search(r"interval", text, re.IGNORECASE):

        return "other"

    if _TEMPORAL.search(text):
        return "temporal"

    if _NUMERIC.search(text):
        return "numeric"

    return "other"


def is_sampled(table):

    return bool(table.approx_rows and table.approx_rows > SAMPLE_ABOVE_ROWS)


def _source(table, column, dialect_key, sampled):
    """The table, or its first rows (only the profiled column) when sampling."""

    if not sampled:

        return table_ref(table, dialect_key)

    inner = (
        exp.select(column_ref(column.name, dialect_key))
        .from_(table_ref(table, dialect_key))
        .limit(SAMPLE_ROWS)
    )

    return exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier("sample")))


def profile_queries(table, column, dialect_key, sampled=None):
    """SQL texts: stats, top values and (numeric/date only) a spread sample."""

    if sampled is None:

        sampled = is_sampled(table)

    col = column_ref(column.name, dialect_key)
    source = _source(table, column, dialect_key, sampled)

    measures = [
        exp.alias_(exp.Count(this=exp.Star()), "total_rows"),
        exp.alias_(exp.Count(this=col.copy()), "non_null"),
    ]

    if not _NO_DISTINCT.search(column.data_type or ""):

        measures.append(
            exp.alias_(exp.Count(this=exp.Distinct(expressions=[col.copy()])), "distinct_values")
        )

    if not _NO_RANGE.search(column.data_type or ""):

        measures.append(exp.alias_(exp.Min(this=col.copy()), "min_value"))
        measures.append(exp.alias_(exp.Max(this=col.copy()), "max_value"))

    queries = {"stats": to_sql(exp.select(*measures).from_(source.copy()), dialect_key)}

    if not _NO_DISTINCT.search(column.data_type or ""):

        top = (
            exp.select(
                exp.alias_(col.copy(), "value"),
                exp.alias_(exp.Count(this=exp.Star()), "occurrences"),
            )
            .from_(source.copy())
            .where(exp.Not(this=exp.Is(this=col.copy(), expression=exp.Null())))
            .group_by(col.copy())
            .order_by(exp.Ordered(this=exp.Count(this=exp.Star()), desc=True))
            .limit(TOP_VALUES)
        )

        queries["top"] = to_sql(top, dialect_key)

    if kind_of(column) in ("numeric", "temporal"):

        spread = (
            exp.select(exp.alias_(col.copy(), "value"))
            .from_(table_ref(table, dialect_key))
            .where(exp.Not(this=exp.Is(this=col.copy(), expression=exp.Null())))
            .limit(SPREAD_ROWS)
        )

        queries["spread"] = to_sql(spread, dialect_key)

    return queries


def summarize(stats):
    """Plain numbers from the one-row stats result (a DataFrame)."""

    if stats is None or stats.empty:

        return {}

    row = stats.iloc[0]

    total = int(row.get("total_rows", 0) or 0)
    non_null = int(row.get("non_null", 0) or 0)

    return {
        "rows": total,
        "nulls": total - non_null,
        "null_share": (total - non_null) / total if total else 0.0,
        "distinct": int(row["distinct_values"]) if "distinct_values" in row and pd.notna(row["distinct_values"]) else None,
        "min": row.get("min_value"),
        "max": row.get("max_value"),
    }


def spread_frame(sample, kind):
    """Clean values for a histogram: numbers or timestamps, bad cells dropped."""

    if sample is None or sample.empty:

        return pd.DataFrame({"value": []})

    values = sample["value"]

    values = (
        pd.to_numeric(values, errors="coerce")
        if kind == "numeric"
        else pd.to_datetime(values, errors="coerce", utc=True).dt.tz_localize(None)
    )

    return pd.DataFrame({"value": values.dropna()})
