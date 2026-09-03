from dataclasses import dataclass

import sqlglot
from sqlglot import exp


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


def validate_sql(sql: str) -> SQLValidation:

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

    if tree.find(exp.Into):

        return SQLValidation(
            False,
            "SELECT INTO is not allowed.",
        )

    if tree.find(exp.Copy):

        return SQLValidation(
            False,
            "COPY is not allowed.",
        )

    return SQLValidation(True)