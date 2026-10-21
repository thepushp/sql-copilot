"""Builds SQL for any supported database from discovered metadata.

Everything is made with sqlglot expressions, so quoting, row limits (LIMIT,
TOP, FETCH FIRST) and paging (OFFSET) come out right for each dialect. The
only text taken from a person is a filter condition, which is parsed and
placed into the WHERE clause of a query built here, never concatenated.
"""

import re

import sqlglot
from sqlglot import exp

from database.dialects import get_dialect


# Columns a grid cannot show meaningfully. JSON and XML are shown as text.
UNSHOWN_TYPES = re.compile(
    r"bytea|blob|binary|image|\braw\b|geometry|geography|vector",
    re.IGNORECASE,
)
_TEXT_TYPES = re.compile(r"char|text|string|clob", re.IGNORECASE)
_LABEL_NAMES = ("name", "title", "label", "code", "description")

MAX_PAGE_ROWS = 500


def ident(name, dialect_key):

    if dialect_key == "oracle":
        quoted = name != name.lower() or not re.fullmatch(r"\w+", name)
    else:
        quoted = True

    return exp.Identifier(this=name, quoted=quoted)


def table_ref(table, dialect_key, alias=None):

    schema = None if dialect_key == "sqlite" else table.schema

    return exp.Table(
        this=ident(table.name, dialect_key),
        db=ident(schema, dialect_key) if schema else None,
        alias=exp.TableAlias(this=exp.to_identifier(alias)) if alias else None,
    )


def column_ref(name, dialect_key, alias=None):

    return exp.Column(
        this=ident(name, dialect_key),
        table=exp.to_identifier(alias) if alias else None,
    )


def to_sql(query, dialect_key):

    return query.sql(dialect=get_dialect(dialect_key).sqlglot)


def qualified_name(table, dialect_key):
    """The table as it should be written in SQL, e.g. "public"."student"."""

    return to_sql(table_ref(table, dialect_key), dialect_key)


def readable(name):

    return name.replace("_", " ")


def table_key(table):

    return f"{table.schema}.{table.name}"


def displayable_columns(table):

    return [c for c in table.columns if not UNSHOWN_TYPES.search(c.data_type)]


def label_column(table):
    """A human-readable column to show for a row of this table, if any."""

    columns = displayable_columns(table)

    for wanted in _LABEL_NAMES:
        for column in columns:
            if column.name.lower() == wanted and _TEXT_TYPES.search(column.data_type):
                return column

    for column in columns:
        if (
            _TEXT_TYPES.search(column.data_type)
            and not column.primary_key
            and not column.name.lower().endswith("_id")
        ):
            return column

    return None


# ------------------------------------------------------------------ filters

_STATEMENT_NODES = (
    exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Alter, exp.Drop, exp.Command, exp.Set, exp.Use,
    exp.Transaction, exp.Commit, exp.Rollback, exp.With,
)


def parse_filter(expression, dialect_key):
    """Turn "status = 'OPEN' AND total > 5" into a condition, or raise ValueError."""

    text = (expression or "").strip().rstrip(";").strip()

    if not text:
        return None

    try:
        statements = sqlglot.parse(text, read=get_dialect(dialect_key).sqlglot)
    except sqlglot.errors.SqlglotError as exc:
        raise ValueError(f"Could not understand the filter: {str(exc).splitlines()[0]}")

    statements = [s for s in statements if s is not None]

    if len(statements) != 1:
        raise ValueError("Enter a single condition, for example: status = 'OPEN'")

    node = statements[0]

    if isinstance(node, _STATEMENT_NODES):
        raise ValueError(
            "Enter a condition such as status = 'OPEN', not a full statement."
        )

    return node


# ------------------------------------------------------------------- paging

def ordered(name, descending, dialect_key):
    """ORDER BY term using the database's own NULL placement.

    Asking for a placement the database does not use by default makes sqlglot
    emulate it with CASE WHEN ... IS NULL, which stops the database using an
    index for the sort. Matching the native behaviour keeps the SQL plain.
    """

    behaviour = sqlglot.Dialect.get_or_raise(
        get_dialect(dialect_key).sqlglot
    ).NULL_ORDERING

    nulls_first = (behaviour == "nulls_are_small") != bool(descending)

    if behaviour == "nulls_are_last":
        nulls_first = False

    return exp.Ordered(
        this=column_ref(name, dialect_key),
        desc=bool(descending),
        nulls_first=nulls_first,
    )


def default_order(table, dialect_key, offset=0):
    """A stable order so pages do not overlap: the primary key when there is one."""

    keys = [c.name for c in table.columns if c.primary_key]

    if keys:
        return [(name, False) for name in keys]

    if offset and dialect_key in ("mssql", "oracle") and table.columns:
        return [(table.columns[0].name, False)]

    return []


def page_query(
    table,
    dialect_key,
    *,
    columns=None,
    where=None,
    order_by=None,
    limit=50,
    offset=0,
):
    """One page of rows with explicit column names (never SELECT *)."""

    chosen = columns or displayable_columns(table) or table.columns

    query = exp.select(
        *[column_ref(c.name, dialect_key) for c in chosen]
    ).from_(table_ref(table, dialect_key))

    if where is not None:
        query = query.where(where)

    for name, descending in (order_by or default_order(table, dialect_key, offset)):
        query = query.order_by(ordered(name, descending, dialect_key))

    query = query.limit(min(max(int(limit), 1), MAX_PAGE_ROWS * 100))

    if offset:
        query = query.offset(int(offset))

    return to_sql(query, dialect_key)


def count_query(table, dialect_key, where=None):

    query = exp.select(
        exp.alias_(exp.Count(this=exp.Star()), "total")
    ).from_(table_ref(table, dialect_key))

    if where is not None:
        query = query.where(where)

    return to_sql(query, dialect_key)


# -------------------------------------------------------------------- joins

def join_query(child, fk, parent, label, dialect_key, limit=10):
    """child rows together with a readable column of the parent they point to."""

    own = [
        c for c in displayable_columns(child)[:6]
        if c.name != fk.column and not c.name.lower().endswith("_id")
    ][:3]

    selected = [column_ref(c.name, dialect_key, "c") for c in own] or [
        column_ref(fk.column, dialect_key, "c")
    ]

    selected.append(
        exp.alias_(
            column_ref(label.name, dialect_key, "p"),
            f"{parent.name}_{label.name}",
            quoted=False,
        )
    )

    query = (
        exp.select(*selected)
        .from_(table_ref(child, dialect_key, "c"))
        .join(
            table_ref(parent, dialect_key, "p"),
            on=exp.EQ(
                this=column_ref(fk.column, dialect_key, "c"),
                expression=column_ref(fk.foreign_column, dialect_key, "p"),
            ),
            join_type="inner",
        )
        .limit(limit)
    )

    return to_sql(query, dialect_key)
