"""Execution-accuracy evaluation: does the generated SQL return the same
result as a hand-written reference query?

The pipeline is injected (`generate`, `execute`, `allowed`) so this module has
no Streamlit or model dependency and is easy to test.
"""

import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field

from services.query_service import NeedsClarification, NoSQLGenerated


PASS = "pass"
WRONG = "wrong_result"
REJECTED = "guard_rejected"
EXEC_ERROR = "exec_error"
NO_SQL = "no_sql"
CLARIFY = "clarify"
GEN_ERROR = "gen_error"
REFERENCE_ERROR = "reference_error"


@dataclass
class EvalCase:
    id: str
    question: str
    sql: str
    ordered: bool = False
    tags: list = field(default_factory=list)


@dataclass
class EvalOutcome:
    case: EvalCase
    status: str
    generated_sql: str = ""
    detail: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0


def load_cases(path) -> list[EvalCase]:

    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    cases = []

    for position, item in enumerate(data, start=1):

        cases.append(
            EvalCase(
                id=str(item.get("id") or f"case-{position}"),
                question=item["question"],
                sql=item["sql"],
                ordered=bool(item.get("ordered", False)),
                tags=list(item.get("tags", [])),
            )
        )

    return cases


def _normal(value):

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "<null>"

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, (int, float)):
        return f"{float(value):.6g}"

    text = str(value).strip()

    try:
        return f"{float(text):.6g}"
    except ValueError:
        return text


def results_match(expected, actual, ordered=False) -> tuple[bool, str]:
    """Same rows, ignoring column names and column order (and row order unless ordered)."""

    if expected.shape[1] != actual.shape[1]:

        return False, (
            f"expected {expected.shape[1]} column(s), got {actual.shape[1]}"
        )

    if len(expected) != len(actual):

        return False, f"expected {len(expected)} row(s), got {len(actual)}"

    def columns(frame):

        cols = [[_normal(v) for v in frame.iloc[:, i]] for i in range(frame.shape[1])]

        return sorted(cols, key=lambda col: sorted(col))

    def rows(cols):

        return list(zip(*cols)) if cols else []

    left, right = rows(columns(expected)), rows(columns(actual))

    if ordered:

        # Keep the reference column order for ordered comparisons.
        left = rows([[_normal(v) for v in expected.iloc[:, i]] for i in range(expected.shape[1])])
        right = rows(columns(actual))

        if left == right:
            return True, ""

        return False, "same size, but the rows or their order differ"

    if Counter(left) == Counter(right):
        return True, ""

    return False, "same size, but the values differ"


def run_case(case, generate, execute, allowed) -> EvalOutcome:
    """generate(question) -> object with sql/input_tokens/output_tokens.
    execute(sql) -> QueryResult. allowed(sql) -> bool (the safety guard)."""

    started = time.perf_counter()

    def done(status, **fields):

        return EvalOutcome(
            case=case,
            status=status,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            **fields,
        )

    expected = execute(case.sql)

    if expected.error:
        return done(REFERENCE_ERROR, detail=expected.error)

    try:

        response = generate(case.question)

    except NeedsClarification as exc:
        return done(CLARIFY, detail=str(exc))

    except NoSQLGenerated as exc:
        return done(NO_SQL, detail=str(exc))

    except Exception as exc:
        return done(GEN_ERROR, detail=str(exc))

    usage = dict(
        generated_sql=response.sql,
        input_tokens=getattr(response, "input_tokens", 0),
        output_tokens=getattr(response, "output_tokens", 0),
    )

    if not allowed(response.sql):
        return done(REJECTED, **usage)

    actual = execute(response.sql)

    if actual.error:
        return done(EXEC_ERROR, detail=actual.error, **usage)

    matched, detail = results_match(expected.rows, actual.rows, case.ordered)

    return done(PASS if matched else WRONG, detail=detail, **usage)


def run_suite(cases, generate, execute, allowed, progress=None) -> list[EvalOutcome]:

    outcomes = []

    for case in cases:

        outcome = run_case(case, generate, execute, allowed)

        outcomes.append(outcome)

        if progress:
            progress(outcome)

    return outcomes


def summarize(outcomes) -> dict:

    total = len(outcomes)
    counts = Counter(outcome.status for outcome in outcomes)

    return {
        "total": total,
        "passed": counts[PASS],
        "accuracy": round(counts[PASS] / total, 3) if total else 0.0,
        "by_status": dict(counts),
        "input_tokens": sum(outcome.input_tokens for outcome in outcomes),
        "output_tokens": sum(outcome.output_tokens for outcome in outcomes),
        "avg_ms": round(sum(o.elapsed_ms for o in outcomes) / total) if total else 0,
    }


def format_report(outcomes) -> str:

    summary = summarize(outcomes)

    lines = [
        f"{summary['passed']}/{summary['total']} correct "
        f"({summary['accuracy']:.0%}), {summary['input_tokens']:,} tokens in / "
        f"{summary['output_tokens']:,} out, {summary['avg_ms']} ms per question",
        "",
    ]

    for outcome in outcomes:

        mark = "ok  " if outcome.status == PASS else "FAIL"

        lines.append(f"{mark} {outcome.case.id}: {outcome.case.question}")

        if outcome.status != PASS:

            lines.append(f"       {outcome.status}: {outcome.detail}".rstrip(": "))

            if outcome.generated_sql:
                lines.append(f"       sql: {outcome.generated_sql}")

    return "\n".join(lines)
