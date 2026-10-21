from dataclasses import dataclass

import sqlglot
from sqlglot import exp


WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Alter,
    exp.Drop,
    exp.TruncateTable,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
)

BLOCKED_FUNCTIONS = {
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "set_config",
    "dblink",
    "dblink_exec",
    "lo_import",
    "lo_export",
    "lo_unlink",
    "query_to_xml",
}


@dataclass
class SQLValidation:
    allowed: bool
    reason: str = ""


def parse_sql(sql: str):

    statements = sqlglot.parse(
        sql,
        read="postgres",
    )

    if len(statements) != 1:

        raise ValueError(
            "Exactly one SQL statement is allowed."
        )

    return statements[0]


def classify_sql(sql: str) -> str:

    tree = parse_sql(sql)

    if isinstance(
        tree,
        (
            exp.Select,
            exp.Union,
            exp.Intersect,
            exp.Except,
        ),
    ):
        return "READ"

    if isinstance(
        tree,
        (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Merge,
        ),
    ):
        return "DML"

    if isinstance(
        tree,
        (
            exp.Create,
            exp.Alter,
            exp.Drop,
            exp.Truncate,
        ),
    ):
        return "DDL"

    return "OTHER"


def _check_policy(tree, policy):

    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
    }

    referenced = []

    for table in tree.find_all(exp.Table):

        name = table.name.lower()
        schema = (table.db or "").lower()

        if not schema and name in cte_names:
            continue

        referenced.append((schema, name))

        for blocked in policy.blocked_tables:

            blocked_schema, blocked_name = blocked.split(".", 1)

            if name == blocked_name and (
                not schema or schema == blocked_schema
            ):

                return (
                    f"Table {blocked} is excluded by the "
                    "privacy settings."
                )

    for column in tree.find_all(exp.Column):

        if column.name.lower() in policy.blocked_columns:

            return (
                f"Column {column.name} is excluded by the "
                "privacy settings."
            )

    stars = [
        star
        for star in tree.find_all(exp.Star)
        if not isinstance(star.parent, exp.Count)
    ]

    if stars:

        for schema, name in referenced:

            for restricted in policy.star_restricted:

                restricted_schema, restricted_name = (
                    restricted.split(".", 1)
                )

                if name == restricted_name and (
                    not schema or schema == restricted_schema
                ):

                    return (
                        f"{restricted} has columns hidden by the "
                        "privacy settings, so SELECT * is not "
                        "allowed. List the columns explicitly."
                    )

    return None


def validate_sql(sql: str, policy=None) -> SQLValidation:

    if not sql or not sql.strip():

        return SQLValidation(
            False,
            "SQL is empty.",
        )

    try:
        tree = parse_sql(sql)

    except Exception as exc:

        return SQLValidation(
            False,
            f"Invalid PostgreSQL SQL: {exc}",
        )

    operation = classify_sql(sql)

    if operation != "READ":

        return SQLValidation(
            False,
            (
                "Only SELECT/WITH queries can execute. "
                f"Detected operation: {operation}"
            ),
        )

    forbidden = tree.find(*WRITE_NODES)

    if forbidden is not None:

        return SQLValidation(
            False,
            (
                "Data-modifying statements are not "
                "allowed, even inside a CTE or "
                f"subquery ({type(forbidden).__name__})."
            ),
        )

    for function in tree.find_all(exp.Func):

        name = (
            function.name
            if isinstance(function, exp.Anonymous)
            else function.sql_name()
        ).lower()

        if name.split(".")[-1] in BLOCKED_FUNCTIONS:

            return SQLValidation(
                False,
                f"Function {name}() is not allowed.",
            )

    if tree.find(exp.Into):

        return SQLValidation(
            False,
            "SELECT INTO is not allowed.",
        )

    if policy is not None:

        violation = _check_policy(tree, policy)

        if violation:

            return SQLValidation(False, violation)

    if tree.find(exp.Copy):

        return SQLValidation(
            False,
            "COPY is not allowed.",
        )

    return SQLValidation(True)