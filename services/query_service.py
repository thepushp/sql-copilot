import re
from dataclasses import dataclass

from database.dialects import get_dialect
from database.sql_guard import classify_sql
from prompts.sql_prompt import (
    CLARIFY_PREFIX,
    NO_SQL_MARKER,
    SQL_PROMPT,
    SUMMARY_PROMPT,
)


MAX_HISTORY_TURNS = 3
SUMMARY_ROWS = 20
SUMMARY_CELL_CHARS = 60


class NoSQLGenerated(ValueError):
    pass


class NeedsClarification(ValueError):
    pass


@dataclass
class QueryResponse:
    sql: str
    operation: str
    input_tokens: int = 0
    output_tokens: int = 0


def build_history(
    messages,
    max_turns: int = MAX_HISTORY_TURNS,
) -> list[tuple[str, str]]:

    turns = []

    for message in messages:

        if message["role"] == "user":

            turns.append(("human", message["content"]))

        elif message.get("sql"):

            turns.append(("ai", message["sql"]))

        elif message.get("clarify"):

            turns.append(("ai", f"{CLARIFY_PREFIX} {message['content']}"))

    return turns[-max_turns * 2:]


def _content_to_text(content) -> str:

    if isinstance(content, str):
        return content

    parts = []

    for block in content:

        if isinstance(block, str):
            parts.append(block)

        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))

    return "".join(parts)


def _usage(response) -> tuple[int, int]:

    usage = getattr(response, "usage_metadata", None) or {}

    return (
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
    )


def _call(llm, messages, on_token=None):
    """Invoke the model, streaming partial text to `on_token` when possible."""

    if on_token is None or not hasattr(llm, "stream"):
        return llm.invoke(messages)

    merged = None
    text = ""

    try:
        for chunk in llm.stream(messages):
            merged = chunk if merged is None else merged + chunk
            piece = _content_to_text(chunk.content)
            if piece:
                text += piece
                on_token(text)
    except Exception:
        if merged is not None:
            raise
        return llm.invoke(messages)

    if merged is None:
        return llm.invoke(messages)

    return merged


def clean_sql(raw: str) -> str:

    text = re.sub(
        r"<think>.*?</think>",
        "",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )

    fenced = re.search(
        r"```(?:sql)?\s*(.*?)```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if fenced:
        text = fenced.group(1)

    return text.strip().rstrip(";").strip()


FOLLOW_UP_NOTE = (
    "PREVIOUS QUERY from this conversation. If the request above refines "
    "it (a filter, column, sort, grouping or limit change), edit this query "
    "and keep everything else; if it is a new topic, ignore it:\n"
)


def _generate(
    llm,
    question,
    context,
    history,
    examples,
    glossary="",
    dialect_key="postgresql",
    previous_sql="",
    on_token=None,
) -> QueryResponse:

    dialect = get_dialect(dialect_key)

    if previous_sql:
        question = f"{question}\n\n{FOLLOW_UP_NOTE}{previous_sql}"

    messages = SQL_PROMPT.invoke(
        {
            "dialect_name": dialect.label,
            "dialect_notes": dialect.notes,
            "limit_hint": dialect.limit_hint,
            "context": context,
            "examples": examples or "None",
            "glossary": glossary.strip() or "None",
            "question": question,
            "history": history or [],
        }
    )

    response = _call(llm, messages, on_token)

    sql = clean_sql(_content_to_text(response.content))

    if not sql:
        raise ValueError("The model returned empty SQL.")

    if sql.upper().startswith(CLARIFY_PREFIX):
        raise NeedsClarification(sql[len(CLARIFY_PREFIX):].strip())

    if NO_SQL_MARKER in sql:
        raise NoSQLGenerated(
            "The retrieved schema does not contain enough "
            "information to answer this. Try rephrasing, "
            "or re-index the database."
        )

    input_tokens, output_tokens = _usage(response)

    return QueryResponse(
        sql=sql,
        operation=classify_sql(sql, dialect_key),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def process_question(
    llm,
    question: str,
    context: str,
    history=None,
    examples: str = "",
    glossary: str = "",
    dialect_key: str = "postgresql",
    previous_sql: str = "",
    on_token=None,
) -> QueryResponse:

    return _generate(
        llm, question, context, history, examples, glossary, dialect_key,
        previous_sql, on_token,
    )


def _retry(
    llm,
    question,
    context,
    history,
    failed_sql,
    feedback,
    examples,
    glossary,
    dialect_key,
    on_token=None,
) -> QueryResponse:

    turns = list(history or [])

    turns.append(("human", question))
    turns.append(("ai", failed_sql))

    return _generate(
        llm, feedback, context, turns, examples, glossary, dialect_key,
        on_token=on_token,
    )


def repair_question(
    llm,
    question: str,
    context: str,
    history,
    failed_sql: str,
    error: str,
    examples: str = "",
    glossary: str = "",
    dialect_key: str = "postgresql",
    on_token=None,
) -> QueryResponse:

    feedback = (
        f"That SQL failed with this {get_dialect(dialect_key).label} error:\n"
        f"{error}\n\n"
        "Return a corrected single statement using only "
        "identifiers from the database context."
    )

    return _retry(
        llm, question, context, history, failed_sql, feedback,
        examples, glossary, dialect_key, on_token,
    )


def refine_question(
    llm,
    question: str,
    context: str,
    history,
    sql: str,
    problems: list[str],
    examples: str = "",
    glossary: str = "",
    dialect_key: str = "postgresql",
    on_token=None,
) -> QueryResponse:
    """Ask for a better query when one ran but returned no rows."""

    feedback = (
        "That SQL ran but returned no rows. Likely problems:\n"
        + "\n".join(f"- {problem}" for problem in problems)
        + "\n\nReturn a corrected single statement. Use the exact stored "
        "spelling of values when they are listed above; otherwise match "
        "with LIKE/ILIKE and wildcards. If the value is a person's name, "
        "look for separate first/last name columns (or a name column on a "
        "related table) and match each word separately."
    )

    return _retry(
        llm, question, context, history, sql, feedback,
        examples, glossary, dialect_key, on_token,
    )


def summarize_result(llm, question: str, dataframe) -> tuple[str, int, int]:

    sample = dataframe.head(SUMMARY_ROWS).astype(str)

    sample = sample.apply(
        lambda column: column.str.slice(0, SUMMARY_CELL_CHARS)
    )

    messages = SUMMARY_PROMPT.invoke(
        {
            "question": question,
            "row_count": len(dataframe),
            "sample": sample.to_csv(index=False),
        }
    )

    response = llm.invoke(messages)

    text = re.sub(
        r"<think>.*?</think>",
        "",
        _content_to_text(response.content),
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    input_tokens, output_tokens = _usage(response)

    return text, input_tokens, output_tokens
