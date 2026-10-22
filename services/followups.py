import re

import pandas as pd


MAX_SUGGESTIONS = 3
MAX_CATEGORIES = 12
DATE_NAME = re.compile(r"(date|_at|_on|time|day|month|year)$", re.IGNORECASE)


def _label(name: str) -> str:

    return str(name).replace("_", " ")


def _is_text(series) -> bool:

    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _is_id(name: str) -> bool:

    lowered = str(name).lower()

    return lowered == "id" or lowered.endswith("_id")


def _already_sorted_by(sql: str, column: str) -> bool:

    match = re.search(r"order\s+by(.*)", sql or "", re.IGNORECASE | re.DOTALL)

    return bool(match) and str(column).lower() in match.group(1).lower()


def suggest_followups(rows, sql: str = "") -> list[str]:
    """Up to three follow-up questions that fit the shape of a result.

    Rule based (no model call). The wording is a normal question, so it goes
    through the usual flow and the chat history supplies the context.
    """

    if rows is None or rows.empty:
        return []

    suggestions = []

    if len(rows) > 10 and not re.search(r"limit\s+10\b", sql or "", re.IGNORECASE):

        suggestions.append("Show only the top 10")

    for column in rows.columns:

        series = rows[column]

        if _is_text(series) and 2 <= series.nunique() <= MAX_CATEGORIES and len(rows) > series.nunique():

            suggestions.append(f"Break this down by {_label(column)}")

            break

    for column in rows.columns:

        series = rows[column]

        if pd.api.types.is_datetime64_any_dtype(series) or (
            DATE_NAME.search(str(column)) and not pd.api.types.is_numeric_dtype(series)
        ):

            suggestions.append("Show this per month")

            break

    for column in rows.columns:

        series = rows[column]

        if (
            pd.api.types.is_numeric_dtype(series)
            and not pd.api.types.is_bool_dtype(series)
            and not _is_id(column)
            and len(rows) > 1
            and not _already_sorted_by(sql, column)
        ):

            suggestions.append(f"Sort by {_label(column)} from highest to lowest")

            break

    return suggestions[:MAX_SUGGESTIONS]
