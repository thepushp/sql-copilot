import sqlglot
from sqlglot import exp

from database.dialects import get_dialect


def _by_name(metadata):

    lookup = {}

    for schema in metadata.schemas:

        for table in schema.tables:

            lookup.setdefault(table.name.lower(), []).append(table)

    return lookup


def qualify_tables(sql: str, metadata, dialect_key: str = "postgresql") -> str:
    """Write every known table as schema.table.

    Models often drop the schema even when told not to. A bare name is only
    rewritten when exactly one table has that name, so nothing is guessed;
    CTE names and already-qualified tables are left alone. SQLite has no
    schemas worth showing, so it is returned unchanged.
    """

    if dialect_key == "sqlite" or metadata is None:
        return sql

    dialect = get_dialect(dialect_key).sqlglot

    try:

        tree = sqlglot.parse_one(sql, read=dialect)

    except Exception:

        return sql

    lookup = _by_name(metadata)

    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}

    changed = False

    for table in tree.find_all(exp.Table):

        if table.db or not table.name or table.name.lower() in ctes:
            continue

        matches = lookup.get(table.name.lower(), [])

        if len(matches) != 1:
            continue

        table.set("db", exp.to_identifier(matches[0].schema))

        changed = True

    return tree.sql(dialect=dialect) if changed else sql


def tables_in_sql(sql: str, metadata, dialect_key: str = "postgresql") -> list[str]:
    """Keys (schema.table) of the known tables a query reads."""

    if not sql or metadata is None:
        return []

    try:

        tree = sqlglot.parse_one(sql, read=get_dialect(dialect_key).sqlglot)

    except Exception:

        return []

    lookup = _by_name(metadata)

    keys = []

    for table in tree.find_all(exp.Table):

        matches = lookup.get(table.name.lower(), [])

        if table.db:

            matches = [item for item in matches if item.schema.lower() == table.db.lower()]

        if matches:

            key = f"{matches[0].schema}.{matches[0].name}"

            if key not in keys:
                keys.append(key)

    return keys


def format_sql(sql: str, dialect_key: str = "postgresql") -> str:
    """Multi-line, consistently indented SQL; the original text if it cannot be parsed."""

    try:

        return sqlglot.parse_one(
            sql, read=get_dialect(dialect_key).sqlglot
        ).sql(dialect=get_dialect(dialect_key).sqlglot, pretty=True)

    except Exception:

        return sql


def finalize_sql(sql: str, metadata, dialect_key: str = "postgresql") -> str:
    """Schema-qualify the tables, then format. Applied to every generated query."""

    return format_sql(qualify_tables(sql, metadata, dialect_key), dialect_key)
