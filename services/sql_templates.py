"""Ready-made SQL for one table: runnable reads, and write/DDL for review only.

Statements that change data or structure are shown so they can be copied, and
are never run by this application.
"""

from dataclasses import dataclass

from database.dialects import get_dialect
from services import sql_builders as sb
from services.table_info import incoming, outgoing


SELECT_ROWS = 100


@dataclass(frozen=True)
class Template:
    label: str
    sql: str
    runnable: bool
    note: str = ""


REVIEW_ONLY = "Review only. This application never runs statements that change data or structure."


def _name(identifier_name, dialect_key):

    return sb.to_sql(sb.ident(identifier_name, dialect_key), dialect_key)


def _columns(table):

    return sb.displayable_columns(table) or table.columns


def select_template(table, dialect_key):

    return Template(
        f"Select the first {SELECT_ROWS} rows",
        sb.page_query(table, dialect_key, limit=SELECT_ROWS),
        True,
    )


def count_template(table, dialect_key):

    return Template("Count the rows", sb.count_query(table, dialect_key), True)


def group_template(table, column, dialect_key):

    from sqlglot import exp

    query = (
        exp.select(
            sb.column_ref(column.name, dialect_key),
            exp.alias_(exp.Count(this=exp.Star()), "total"),
        )
        .from_(sb.table_ref(table, dialect_key))
        .group_by(sb.column_ref(column.name, dialect_key))
        .order_by(sb.ordered("total", True, dialect_key))
        .limit(50)
    )

    return Template(
        f"Rows per {column.name}",
        sb.to_sql(query, dialect_key),
        True,
    )


def parent_join_templates(table, index, dialect_key):
    """One query joining a readable column of each table this one points to."""

    found = []

    for ref in outgoing(table, index):

        parent = index[ref.other_key]
        label = sb.label_column(parent)

        if label is None or parent is table:
            continue

        fk = next(f for f in table.foreign_keys if f.column == ref.column)

        found.append(
            Template(
                f"Join to {parent.name} (through {ref.column})",
                sb.join_query(table, fk, parent, label, dialect_key, SELECT_ROWS),
                True,
                "inferred from the column name" if ref.inferred else "",
            )
        )

    return found


def child_join_templates(table, key, index, dialect_key):
    """Queries listing the rows of tables that point at this one."""

    found = []

    for ref in incoming(key, index):

        child = index[ref.other_key]
        label = sb.label_column(table)

        if label is None or child is table:
            continue

        fk = next(
            f for f in child.foreign_keys
            if f.column == ref.other_column
            and f"{f.foreign_schema}.{f.foreign_table}" == key
        )

        found.append(
            Template(
                f"Rows of {child.name} that point here",
                sb.join_query(child, fk, table, label, dialect_key, SELECT_ROWS),
                True,
                "inferred from the column name" if ref.inferred else "",
            )
        )

    return found


# -------------------------------------------------------- review-only writes

def insert_template(table, dialect_key):

    columns = _columns(table)

    return Template(
        "Insert a row",
        "INSERT INTO {table}\n  ({names})\nVALUES\n  ({values});".format(
            table=sb.qualified_name(table, dialect_key),
            names=", ".join(_name(c.name, dialect_key) for c in columns),
            values=", ".join(f":{c.name}" for c in columns),
        ),
        False,
        REVIEW_ONLY + " Replace each :name with a value.",
    )


def _where_keys(table, dialect_key):

    keys = [c for c in table.columns if c.primary_key] or table.columns[:1]

    return " AND ".join(
        f"{_name(c.name, dialect_key)} = :{c.name}" for c in keys
    )


def update_template(table, dialect_key):

    editable = [c for c in _columns(table) if not c.primary_key] or _columns(table)

    return Template(
        "Update a row",
        "UPDATE {table}\nSET {assignments}\nWHERE {where};".format(
            table=sb.qualified_name(table, dialect_key),
            assignments=",\n    ".join(
                f"{_name(c.name, dialect_key)} = :{c.name}" for c in editable
            ),
            where=_where_keys(table, dialect_key),
        ),
        False,
        REVIEW_ONLY + " Always keep the WHERE clause.",
    )


def delete_template(table, dialect_key):

    return Template(
        "Delete a row",
        "DELETE FROM {table}\nWHERE {where};".format(
            table=sb.qualified_name(table, dialect_key),
            where=_where_keys(table, dialect_key),
        ),
        False,
        REVIEW_ONLY + " Always keep the WHERE clause.",
    )


# ------------------------------------------------------------------- DDL

def create_table_ddl(table, index, dialect_key):
    """CREATE TABLE rebuilt from what was discovered (not the original text)."""

    if table.kind != "table":
        return Template(
            f"Create {table.kind}",
            f"-- The definition of a {table.kind} is not available yet.",
            False,
            REVIEW_ONLY,
        )

    lines = []

    for column in table.columns:

        line = f"  {_name(column.name, dialect_key)} {column.data_type}"

        if column.nullable == "NO":
            line += " NOT NULL"

        if column.default is not None:
            line += f" DEFAULT {column.default}"

        lines.append(line)

    keys = [c.name for c in table.columns if c.primary_key]

    if keys:
        lines.append(
            "  PRIMARY KEY ("
            + ", ".join(_name(k, dialect_key) for k in keys)
            + ")"
        )

    for fk in table.foreign_keys:

        target_key = f"{fk.foreign_schema}.{fk.foreign_table}"

        if fk.inferred or target_key not in index:
            continue

        lines.append(
            f"  FOREIGN KEY ({_name(fk.column, dialect_key)}) REFERENCES "
            f"{sb.qualified_name(index[target_key], dialect_key)} "
            f"({_name(fk.foreign_column, dialect_key)})"
        )

    ddl = "CREATE TABLE {table} (\n{body}\n);".format(
        table=sb.qualified_name(table, dialect_key),
        body=",\n".join(lines),
    )

    return Template(
        "Create the table (DDL)",
        ddl,
        False,
        REVIEW_ONLY + " Rebuilt from the discovered columns, keys and defaults; "
        "check it against the real definition before using it.",
    )


def index_ddl(table, dialect_key):

    statements = []

    for index in table.indexes:

        if index.definition:
            statements.append(index.definition.rstrip(";") + ";")
        elif index.columns:
            statements.append(
                "CREATE {unique}INDEX {name} ON {table} ({columns});".format(
                    unique="UNIQUE " if index.unique else "",
                    name=_name(index.name, dialect_key),
                    table=sb.qualified_name(table, dialect_key),
                    columns=", ".join(_name(c, dialect_key) for c in index.columns),
                )
            )

    if not statements:
        return None

    return Template(
        "Create the indexes",
        "\n".join(statements),
        False,
        REVIEW_ONLY,
    )


def generate_templates(table, index, dialect_key):
    """Every template that makes sense for this table, runnable ones first."""

    key = sb.table_key(table)

    templates = [select_template(table, dialect_key), count_template(table, dialect_key)]

    templates += parent_join_templates(table, index, dialect_key)
    templates += child_join_templates(table, key, index, dialect_key)

    if table.kind == "table":
        templates += [
            insert_template(table, dialect_key),
            update_template(table, dialect_key),
            delete_template(table, dialect_key),
        ]

    templates.append(create_table_ddl(table, index, dialect_key))

    indexes = index_ddl(table, dialect_key)

    if indexes:
        templates.append(indexes)

    return templates
