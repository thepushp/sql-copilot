"""Checks around a generated query: its plan before it matters, and whether
the result looks plausible afterwards.

Everything here only warns or suggests a retry. Blocking stays in
`database/sql_guard.py`.
"""

import json
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from database.dialects import get_dialect
from database.query_executor import execute_query
from services.sql_builders import column_ref, table_ref, to_sql


BIG_SCAN_ROWS = 1_000_000

# Dialects whose EXPLAIN gives usable row estimates without running the query.
EXPLAIN_DIALECTS = ("postgresql", "mysql")


@dataclass
class PlanEstimate:
    rows: int | None = None
    cost: float | None = None
    big_scans: list = field(default_factory=list)


@dataclass
class Issue:
    kind: str
    text: str
    fixable: bool = False
    target: tuple | None = None  # (TableMetadata, ColumnMetadata, literal)


# ---------------------------------------------------------------------------
# Plan (EXPLAIN)
# ---------------------------------------------------------------------------


def _walk_postgres(node, scans):

    if node.get("Node Type") == "Seq Scan":

        scans.append(
            (node.get("Relation Name", "a table"), int(node.get("Plan Rows", 0)))
        )

    for child in node.get("Plans", []) or []:

        _walk_postgres(child, scans)


def estimate_plan(datasource_name, sql, config, dialect_key) -> PlanEstimate | None:
    """Ask the database for its plan. Never runs the query; None if unsupported."""

    if dialect_key not in EXPLAIN_DIALECTS:
        return None

    prefix = "EXPLAIN (FORMAT JSON) " if dialect_key == "postgresql" else "EXPLAIN "

    result = execute_query(datasource_name, prefix + sql, config)

    if result.error or result.rows is None or result.rows.empty:
        return None

    try:

        if dialect_key == "postgresql":

            data = result.rows.iloc[0, 0]

            if isinstance(data, str):
                data = json.loads(data)

            plan = data[0]["Plan"]

            scans = []

            _walk_postgres(plan, scans)

            return PlanEstimate(
                rows=int(plan.get("Plan Rows", 0)),
                cost=float(plan.get("Total Cost", 0)),
                big_scans=[item for item in scans if item[1] >= BIG_SCAN_ROWS],
            )

        frame = result.rows
        frame.columns = [str(column).lower() for column in frame.columns]

        scans = [
            (str(row["table"]), int(row["rows"]))
            for _, row in frame.iterrows()
            if str(row.get("type", "")).upper() == "ALL"
            and str(row.get("rows", "")).isdigit()
            and int(row["rows"]) >= BIG_SCAN_ROWS
        ]

        return PlanEstimate(big_scans=scans)

    except Exception:

        return None


def plan_warnings(estimate) -> list[str]:

    if estimate is None:
        return []

    return [
        f"Reads about {rows:,} rows of {table} without an index, which can be "
        "slow. A tighter filter on an indexed column would help."
        for table, rows in estimate.big_scans
    ]


def plan_label(estimate) -> str:

    if estimate is None or not estimate.rows:
        return ""

    return f"~{estimate.rows:,} rows estimated"


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _table_lookup(metadata):

    lookup = {}

    for schema in metadata.schemas:

        for table in schema.tables:

            lookup.setdefault(table.name.lower(), []).append(table)

    return lookup


def _alias_map(tree, lookup):
    """alias or name (lower) -> TableMetadata for tables the query reads."""

    aliases = {}

    for table in tree.find_all(exp.Table):

        candidates = lookup.get(table.name.lower(), [])

        if table.db:

            candidates = [
                item for item in candidates if item.schema.lower() == table.db.lower()
            ]

        if candidates:

            aliases[(table.alias_or_name or table.name).lower()] = candidates[0]

    return aliases


def _resolve(column, aliases):
    """(alias, TableMetadata, ColumnMetadata) for a column, or None."""

    name = column.name.lower()

    if column.table:

        table = aliases.get(column.table.lower())

        candidates = [(column.table.lower(), table)] if table else []

    else:

        candidates = [
            (alias, table)
            for alias, table in aliases.items()
            if any(item.name.lower() == name for item in table.columns)
        ]

    if len(candidates) != 1:
        return None

    alias, table = candidates[0]

    for item in table.columns:

        if item.name.lower() == name:
            return alias, table, item

    return None


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------


def _cartesian(tree) -> bool:

    for select in tree.find_all(exp.Select):

        where = select.args.get("where")

        linked = where is not None and any(
            isinstance(item.left, exp.Column) and isinstance(item.right, exp.Column)
            for item in where.find_all(exp.EQ)
        )

        for join in select.args.get("joins") or []:

            if join.args.get("on") or join.args.get("using"):
                continue

            if not isinstance(join.this, (exp.Table, exp.Subquery)):
                continue

            if not linked:
                return True

    return False


def _fan_out(tree, aliases, dialect) -> list[Issue]:
    """SUM/AVG of a parent column joined to its child table counts rows repeatedly."""

    pairs = []

    for equality in tree.find_all(exp.EQ):

        if isinstance(equality.left, exp.Column) and isinstance(equality.right, exp.Column):

            left = _resolve(equality.left, aliases)
            right = _resolve(equality.right, aliases)

            if left and right and left[0] != right[0]:
                pairs.append((left, right))

    issues = []

    for aggregate in tree.find_all(exp.Sum, exp.Avg):

        if aggregate.find(exp.Distinct):
            continue

        resolved = [_resolve(item, aliases) for item in aggregate.find_all(exp.Column)]

        resolved = [item for item in resolved if item]

        if not resolved or len({item[0] for item in resolved}) != 1:
            continue

        parent_alias, parent, _ = resolved[0]

        for left, right in pairs:

            for one, many in ((left, right), (right, left)):

                if one[0] != parent_alias:
                    continue

                child = many[1]

                if any(
                    key.column.lower() == many[2].name.lower()
                    and key.foreign_table.lower() == parent.name.lower()
                    and key.foreign_column.lower() == one[2].name.lower()
                    for key in child.foreign_keys
                ):

                    issues.append(
                        Issue(
                            "fan_out",
                            f"{aggregate.sql(dialect=dialect)} reads {parent.name} "
                            f"but is joined to {child.name}, which has many rows "
                            f"per {parent.name}, so values may be counted more "
                            "than once. Aggregate before joining, or use "
                            "DISTINCT.",
                        )
                    )

                    return issues

    return issues


# ---------------------------------------------------------------------------
# Result checks
# ---------------------------------------------------------------------------


_TEXT_TYPE = re.compile(r"char|text|string|clob|name", re.IGNORECASE)
MAX_VALUE_ISSUES = 4
MAX_PROBE_ROWS = 5_000_000


def _squash(value: str) -> str:

    return re.sub(r"[^a-z0-9]", "", value.lower())


def _unknown_values(tree, aliases) -> list[Issue]:
    """String filters that matched nothing (only used for empty results)."""

    checks = []

    for equality in tree.find_all(exp.EQ):

        for column, literal in (
            (equality.left, equality.right),
            (equality.right, equality.left),
        ):

            if isinstance(column, exp.Column) and isinstance(literal, exp.Literal) and literal.is_string:
                checks.append((column, [literal.name]))

    for member in tree.find_all(exp.In):

        if isinstance(member.this, exp.Column):

            literals = [
                item.name
                for item in member.expressions
                if isinstance(item, exp.Literal) and item.is_string
            ]

            if literals:
                checks.append((member.this, literals))

    issues = []

    for column, literals in checks:

        resolved = _resolve(column, aliases)

        if not resolved:
            continue

        _, table, metadata = resolved

        stored = metadata.values

        for literal in literals:

            if literal in stored:
                continue

            target = (table, metadata, literal)

            if stored:

                similar = next(
                    (item for item in stored if _squash(item) == _squash(literal)),
                    None,
                )

                if similar:

                    text = (
                        f"{table.name}.{metadata.name} = '{literal}' is stored "
                        f"as '{similar}' (spelling, case or separators differ)."
                    )

                else:

                    kind = "allowed" if metadata.values_kind == "enum" else "common"

                    text = (
                        f"'{literal}' is not one of the {kind} values of "
                        f"{table.name}.{metadata.name} "
                        f"({', '.join(stored[:8])})."
                    )

            elif _TEXT_TYPE.search(metadata.data_type or ""):

                text = (
                    f"No row has {table.name}.{metadata.name} exactly equal to "
                    f"'{literal}'. The stored spelling or format may differ."
                )

            else:

                continue

            issues.append(Issue("unknown_value", text, fixable=True, target=target))

            if len(issues) >= MAX_VALUE_ISSUES:
                return issues

    return issues


def find_stored_values(issues, datasource_name, config, dialect_key, allowed, limit=8) -> list[str]:
    """Look up what is really stored for the values that matched nothing.

    One small read per issue: DISTINCT values containing the words of the
    filter. Each query goes through `allowed(sql)` (the safety guard with the
    privacy policy), so hidden columns are never read.
    """

    hints = []

    for issue in issues:

        if issue.target is None:
            continue

        table, column, literal = issue.target

        if (table.approx_rows or 0) > MAX_PROBE_ROWS:
            continue

        words = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", literal) if len(word) >= 2]

        if not words:
            continue

        attempts = [words]

        if len(words) > 1:
            attempts.append([max(words, key=len)])

        for attempt in attempts:

            lowered = exp.Lower(this=column_ref(column.name, dialect_key))

            condition = exp.and_(
                *[exp.Like(this=lowered.copy(), expression=exp.Literal.string(f"%{word}%")) for word in attempt]
            )

            query = (
                exp.select(column_ref(column.name, dialect_key))
                .distinct()
                .from_(table_ref(table, dialect_key))
                .where(condition)
                .limit(limit)
            )

            sql = to_sql(query, dialect_key)

            if not allowed(sql):
                break

            result = execute_query(datasource_name, sql, config)

            if result.error or result.rows is None or result.rows.empty:
                continue

            values = [str(value) for value in result.rows.iloc[:, 0].tolist() if value is not None]

            if values:

                hints.append(
                    f"{table.name}.{column.name} actually stores values such as: "
                    + ", ".join(f"'{value}'" for value in values)
                )

                break

    return hints


def review(sql, rows, dialect_key, metadata) -> list[Issue]:
    """Plausibility problems for a query that ran. Empty list means nothing found."""

    dialect = get_dialect(dialect_key).sqlglot

    try:

        tree = sqlglot.parse_one(sql, read=dialect)

    except Exception:

        return []

    issues = []

    if _cartesian(tree):

        issues.append(
            Issue(
                "cartesian",
                "A table is joined without a join condition, so every row is "
                "paired with every row of the other table.",
            )
        )

    aliases = _alias_map(tree, _table_lookup(metadata)) if metadata is not None else {}

    issues.extend(_fan_out(tree, aliases, dialect))

    if rows is not None and rows.empty:

        issues.extend(_unknown_values(tree, aliases))

    elif rows is not None:

        empty_columns = [str(name) for name in rows.columns if rows[name].isna().all()]

        if empty_columns:

            issues.append(
                Issue(
                    "all_null",
                    "Empty in every row: " + ", ".join(empty_columns[:4]) + ". "
                    "A join or filter may be pointing at the wrong column.",
                )
            )

    return issues
