import re

from dataclasses import dataclass


@dataclass
class SQLValidation:

    allowed: bool
    reason: str = ""


FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "UPSERT",
    "TRUNCATE",
    "CREATE",
    "ALTER",
    "DROP",
    "GRANT",
    "REVOKE",
    "COMMENT",
    "VACUUM",
    "REINDEX",
    "CALL",
    "DO",
    "COPY",
}


def _strip_comments(
    sql: str,
) -> str:

    sql = re.sub(
        r"/\*.*?\*/",
        " ",
        sql,
        flags=re.DOTALL,
    )

    return re.sub(
        r"--.*?$",
        " ",
        sql,
        flags=re.MULTILINE,
    )


def validate_sql(
    sql: str,
) -> SQLValidation:

    if not sql or not sql.strip():

        return SQLValidation(
            False,
            "Empty SQL.",
        )

    cleaned = (
        _strip_comments(sql)
        .strip()
    )

    statements = [
        statement.strip()
        for statement in cleaned.split(";")
        if statement.strip()
    ]

    if len(statements) != 1:

        return SQLValidation(
            False,
            "Multiple SQL statements "
            "are not allowed.",
        )

    statement = statements[0]

    if not re.match(
        r"^\s*(SELECT|WITH)\b",
        statement,
        re.IGNORECASE,
    ):

        return SQLValidation(
            False,
            "Only SELECT/WITH read-only "
            "queries can be executed.",
        )

    upper_sql = statement.upper()

    for keyword in FORBIDDEN_KEYWORDS:

        if re.search(
            rf"\b{re.escape(keyword)}\b",
            upper_sql,
        ):

            return SQLValidation(
                False,
                f"Forbidden SQL operation "
                f"detected: {keyword}.",
            )

    if re.search(
        r"\bINTO\s+(?:TEMP(?:ORARY)?\s+)?",
        upper_sql,
    ):

        return SQLValidation(
            False,
            "SELECT INTO is not allowed.",
        )

    return SQLValidation(True)