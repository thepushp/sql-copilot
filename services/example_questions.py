import re
from dataclasses import dataclass, field

from sqlglot import exp

from database.dialects import get_dialect
from services import sql_builders
from rag.retriever import DERIVED_MARKERS
from rag.textutil import matches, words


MAX_CANDIDATES = 12
LARGE_TABLE_ROWS = 2_000_000
MAX_COLUMNS = 6
PREVIEW_ROWS = 10

_LABEL_NAMES = ("name", "title", "label", "code", "description")
_SKIP_TYPES = re.compile(
    r"bytea|blob|binary|json|xml|geometry|geography|vector|image|clob|raw",
    re.IGNORECASE,
)
_TEXT_TYPES = re.compile(r"char|text|string", re.IGNORECASE)
_TIME_TYPES = re.compile(r"date|time", re.IGNORECASE)


@dataclass
class ExampleQuestion:
    question: str
    sql: str
    kind: str
    tables: list[str] = field(default_factory=list)


_ident = sql_builders.ident
_table = sql_builders.table_ref
_column = sql_builders.column_ref
_sql = sql_builders.to_sql
_readable = sql_builders.readable
_key = sql_builders.table_key


def _is_large(table):

    return bool(table.approx_rows and table.approx_rows > LARGE_TABLE_ROWS)


def _usable_columns(table):

    return [
        column
        for column in table.columns
        if not _SKIP_TYPES.search(column.data_type)
    ]


def _is_derived(table):

    return any(
        matches(word, DERIVED_MARKERS)
        for word in words(f"{table.schema} {table.name}".replace("_", " "))
    )


def _rank_tables(metadata):
    """Business tables first: many rows, well connected, not derived."""

    tables = [
        table
        for schema in metadata.schemas
        for table in schema.tables
        if _usable_columns(table)
    ]

    degree = {_key(table): len(table.foreign_keys) for table in tables}

    for table in tables:
        for fk in table.foreign_keys:
            parent = f"{fk.foreign_schema}.{fk.foreign_table}"
            degree[parent] = degree.get(parent, 0) + 1

    def score(table):

        rows = table.approx_rows if table.approx_rows and table.approx_rows > 0 else 1

        return (
            not _is_derived(table),
            table.kind == "table",
            degree.get(_key(table), 0) > 0,
            rows,
            degree.get(_key(table), 0),
            table.name,
        )

    return sorted(tables, key=score, reverse=True)


_label_column = sql_builders.label_column


def _preview_columns(table):

    return [c for c in _usable_columns(table)][:MAX_COLUMNS]


def _count_example(table, dialect_key):

    query = exp.select(
        exp.alias_(exp.Count(this=exp.Star()), "total")
    ).from_(_table(table, dialect_key))

    return ExampleQuestion(
        f"How many rows are in {_readable(table.name)}?",
        _sql(query, dialect_key),
        "count",
        [_key(table)],
    )


def _preview_example(table, dialect_key):

    query = (
        exp.select(*[_column(c.name, dialect_key) for c in _preview_columns(table)])
        .from_(_table(table, dialect_key))
        .limit(PREVIEW_ROWS)
    )

    return ExampleQuestion(
        f"Show the first {PREVIEW_ROWS} rows of {_readable(table.name)}",
        _sql(query, dialect_key),
        "preview",
        [_key(table)],
    )


def _group_example(table, column, dialect_key):

    group_column = _column(column.name, dialect_key)

    query = (
        exp.select(group_column, exp.alias_(exp.Count(this=exp.Star()), "total"))
        .from_(_table(table, dialect_key))
        .group_by(_column(column.name, dialect_key))
        .order_by(sql_builders.ordered("total", True, dialect_key))
    )

    return ExampleQuestion(
        f"How many rows per {_readable(column.name)} in {_readable(table.name)}?",
        _sql(query, dialect_key),
        "group",
        [_key(table)],
    )


def _recent_example(table, column, dialect_key):

    query = (
        exp.select(*[_column(c.name, dialect_key) for c in _preview_columns(table)])
        .from_(_table(table, dialect_key))
        .where(exp.Not(this=exp.Is(this=_column(column.name, dialect_key), expression=exp.Null())))
        .order_by(exp.Ordered(this=_column(column.name, dialect_key), desc=True))
        .limit(PREVIEW_ROWS)
    )

    return ExampleQuestion(
        f"Show the {PREVIEW_ROWS} most recent rows of {_readable(table.name)}",
        _sql(query, dialect_key),
        "recent",
        [_key(table)],
    )


def _join_example(child, fk, parent, label, dialect_key):

    return ExampleQuestion(
        f"List {_readable(child.name)} together with their {_readable(parent.name)}",
        sql_builders.join_query(child, fk, parent, label, dialect_key, PREVIEW_ROWS),
        "join",
        [_key(child), _key(parent)],
    )


def build_candidates(metadata, dialect_key="postgresql"):
    """Example questions grounded in the schema; they still need verifying."""

    ranked = _rank_tables(metadata)

    if not ranked:
        return []

    by_key = {_key(table): table for table in ranked}

    scannable = [table for table in ranked if not _is_large(table)]

    candidates = []

    if scannable:
        candidates.append(_count_example(scannable[0], dialect_key))

    for table in scannable:

        column = next(
            (c for c in table.columns if 2 <= len(c.values) <= 12 and not c.primary_key),
            None,
        )

        if column:
            candidates.append(_group_example(table, column, dialect_key))
            break

    explicit_first = sorted(
        (
            (child, fk)
            for child in ranked
            for fk in child.foreign_keys
        ),
        key=lambda pair: pair[1].inferred,
    )

    for child, fk in explicit_first:

        parent = by_key.get(f"{fk.foreign_schema}.{fk.foreign_table}")

        label = _label_column(parent) if parent else None

        if parent and label and parent is not child:
            candidates.append(_join_example(child, fk, parent, label, dialect_key))
            break

    for table in scannable:

        column = next(
            (c for c in _usable_columns(table) if _TIME_TYPES.search(c.data_type)),
            None,
        )

        if column:
            candidates.append(_recent_example(table, column, dialect_key))
            break

    for table in ranked[:4]:
        candidates.append(_preview_example(table, dialect_key))

    for table in scannable[1:4]:
        candidates.append(_count_example(table, dialect_key))

    seen = set()
    unique = []

    for candidate in candidates:
        if candidate.sql not in seen:
            seen.add(candidate.sql)
            unique.append(candidate)

    return unique[:MAX_CANDIDATES]


def _worth_showing(candidate, rows):

    if rows is None or rows.empty:
        return False

    if candidate.kind == "count":
        try:
            return float(rows.iloc[0, 0]) > 0
        except (TypeError, ValueError):
            return False

    if candidate.kind == "group":
        return len(rows) >= 2

    return True


def verify_examples(candidates, run, allowed, limit=4):
    """Keep candidates that pass the safety check and return real data.

    run(sql) -> QueryResult-like (rows, error); allowed(sql) -> bool.
    One example per kind first, so the list stays varied.
    """

    chosen = []
    used_kinds = set()

    for pass_number in (0, 1):

        for candidate in candidates:

            if len(chosen) >= limit:
                return chosen

            if candidate in chosen:
                continue

            if pass_number == 0 and candidate.kind in used_kinds:
                continue

            if not allowed(candidate.sql):
                continue

            result = run(candidate.sql)

            if result.error or not _worth_showing(candidate, result.rows):
                continue

            chosen.append(candidate)
            used_kinds.add(candidate.kind)

    return chosen
