from dataclasses import dataclass

from database.sql_guard import classify_sql
from prompts.sql_prompt import SQL_PROMPT


@dataclass
class QueryResponse:
    sql: str
    operation: str


def process_question(
    llm,
    question: str,
    context: str,
) -> QueryResponse:

    messages = SQL_PROMPT.invoke(
        {
            "context": context,
            "question": question,
        }
    )

    response = llm.invoke(messages)

    raw_sql = response.content.strip()

    # ---------------------------------------------------------
    # Remove accidental markdown fences
    # ---------------------------------------------------------

    if raw_sql.startswith("```sql"):
        raw_sql = raw_sql[6:]

    elif raw_sql.startswith("```"):
        raw_sql = raw_sql[3:]

    if raw_sql.endswith("```"):
        raw_sql = raw_sql[:-3]

    raw_sql = raw_sql.strip()

    if not raw_sql:
        raise ValueError(
            "The model returned empty SQL."
        )

    operation = classify_sql(raw_sql)

    return QueryResponse(
        sql=raw_sql,
        operation=operation,
    )