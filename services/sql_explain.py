import sqlglot
from sqlglot import exp

from database.dialects import get_dialect


MAX_FILTERS = 3
MAX_TEXT = 70

AGGREGATE_LABELS = {
    "COUNT": "number of",
    "SUM": "total of",
    "AVG": "average of",
    "MIN": "lowest",
    "MAX": "highest",
}


def _short(text: str) -> str:

    text = " ".join(text.split())

    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 3] + "..."


def _conditions(node) -> list:

    if isinstance(node, exp.And):

        return _conditions(node.left) + _conditions(node.right)

    if isinstance(node, exp.Paren) and isinstance(node.this, exp.And):

        return _conditions(node.this)

    return [node]


def _aggregate_phrase(aggregate, dialect) -> str:

    label = AGGREGATE_LABELS.get(aggregate.key.upper())

    if label is None:
        return ""

    argument = aggregate.this

    if argument is None or isinstance(argument, exp.Star):
        return "number of rows" if label == "number of" else ""

    return f"{label} {_short(argument.sql(dialect=dialect))}"


def describe_sql(sql: str, dialect_key: str = "postgresql") -> str:
    """One plain-English line describing what a SELECT does.

    Built from the parsed SQL, so it costs no model call and never invents
    anything the query does not contain. Returns "" if it cannot be parsed.
    """

    dialect = get_dialect(dialect_key).sqlglot

    try:

        tree = sqlglot.parse_one(sql, read=dialect)

    except Exception:

        return ""

    if isinstance(tree, (exp.Union, exp.Intersect, exp.Except)):

        return "Combines the results of several queries"

    if not isinstance(tree, exp.Select):

        return ""

    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}

    names = []

    for table in tree.find_all(exp.Table):

        if table.name and table.name not in ctes and table.name not in names:

            names.append(table.name)

    parts = []

    if names:

        parts.append(
            f"Reads {names[0]}"
            + (f" joined with {', '.join(names[1:])}" if names[1:] else "")
        )

    aggregates = []

    for projection in tree.expressions:

        for aggregate in projection.find_all(exp.AggFunc):

            phrase = _aggregate_phrase(aggregate, dialect)

            if phrase and phrase not in aggregates:
                aggregates.append(phrase)

    if aggregates:

        parts.append("computes " + ", ".join(aggregates[:3]))

    group = tree.args.get("group")

    if group is not None and group.expressions:

        parts.append(
            "grouped by "
            + ", ".join(
                _short(item.sql(dialect=dialect))
                for item in group.expressions[:3]
            )
        )

    where = tree.args.get("where")

    if where is not None:

        conditions = [
            _short(item.sql(dialect=dialect))
            for item in _conditions(where.this)
        ]

        text = "; ".join(conditions[:MAX_FILTERS])

        if len(conditions) > MAX_FILTERS:
            text += f" (+{len(conditions) - MAX_FILTERS} more)"

        parts.append("filtered by " + text)

    having = tree.args.get("having")

    if having is not None:

        parts.append("keeping groups where " + _short(having.this.sql(dialect=dialect)))

    order = tree.args.get("order")

    if order is not None and order.expressions:

        first = order.expressions[0]

        direction = "highest first" if first.args.get("desc") else "lowest first"

        parts.append(
            f"sorted by {_short(first.this.sql(dialect=dialect))} ({direction})"
        )

    limit = tree.args.get("limit")

    if limit is not None:

        count = limit.args.get("expression")

        if count is not None and count.sql().isdigit():
            parts.append(f"first {count.sql()} rows")

    return " · ".join(parts)
