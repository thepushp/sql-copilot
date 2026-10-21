from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from database.dialects import get_dialect


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

@dataclass
class SQLValidation:
    allowed: bool
    reason: str = ""


def parse_sql(sql: str, read: str = "postgres"):

    statements = sqlglot.parse(
        sql,
        read=read,
    )

    if len(statements) != 1:

        raise ValueError(
            "Exactly one SQL statement is allowed."
        )

    return statements[0]


def classify_sql(sql: str, dialect_key: str = "postgresql") -> str:

    tree = parse_sql(sql, get_dialect(dialect_key).sqlglot)

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
            exp.TruncateTable,
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


def validate_sql(
    sql: str,
    policy=None,
    dialect_key: str = "postgresql",
) -> SQLValidation:

    dialect = get_dialect(dialect_key)

    if not sql or not sql.strip():

        return SQLValidation(
            False,
            "SQL is empty.",
        )

    try:
        tree = parse_sql(sql, dialect.sqlglot)

    except Exception as exc:

        return SQLValidation(
            False,
            f"Invalid {dialect.label} SQL: {exc}",
        )

    operation = classify_sql(sql, dialect_key)

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

    if tree.find(exp.Lock):

        return SQLValidation(
            False,
            "Locking reads (FOR UPDATE / FOR SHARE) are not allowed.",
        )

    for function in tree.find_all(exp.Func):

        name = (
            function.name
            if isinstance(function, exp.Anonymous)
            else function.sql_name()
        ).lower()

        if name.split(".")[-1] in dialect.forbidden_functions:

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