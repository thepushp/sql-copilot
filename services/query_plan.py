"""Show how the database would run a query, without running it."""

from database.query_executor import execute_query


PLAN_DIALECTS = ("postgresql", "mysql", "sqlite")

NO_PLAN = {
    "mssql": "SQL Server needs session settings that a read-only login cannot change.",
    "oracle": "Oracle writes plans to a table, which this read-only tool never does.",
}


def _postgres_lines(rows):

    return "\n".join(str(value) for value in rows.iloc[:, 0])


def _sqlite_lines(rows):

    lines = []
    depth = {0: 0}

    for _, row in rows.iterrows():

        parent = int(row.iloc[1])
        level = depth.get(parent, 0)
        depth[int(row.iloc[0])] = level + 1
        lines.append("  " * level + str(row.iloc[3]))

    return "\n".join(lines)


def _mysql_lines(rows):

    return rows.fillna("").astype(str).to_string(index=False)


def explain(datasource_name, sql, config, dialect_key):
    """(plan text, error). The query is only planned, never executed."""

    if dialect_key not in PLAN_DIALECTS:

        return None, NO_PLAN.get(dialect_key, "Plans are not available for this database.")

    prefix = "EXPLAIN QUERY PLAN " if dialect_key == "sqlite" else "EXPLAIN "

    result = execute_query(datasource_name, prefix + sql, config)

    if result.error:

        return None, result.error

    rows = result.rows

    if rows is None or rows.empty:

        return None, "The database returned no plan."

    if dialect_key == "postgresql":

        return _postgres_lines(rows), None

    if dialect_key == "sqlite":

        return _sqlite_lines(rows), None

    return _mysql_lines(rows), None
