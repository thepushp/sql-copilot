from dataclasses import dataclass

from langchain_core.prompts import (
    ChatPromptTemplate,
)

from database.sql_guard import classify_sql


@dataclass
class QueryResponse:

    sql: str
    operation: str


SQL_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are an expert PostgreSQL database assistant.

Convert the user's request into exactly ONE
PostgreSQL SQL statement.

DATABASE ACCESS

The database context can contain objects from
ANY accessible schema.

There is NO restriction to the public schema.

You may use:
- public
- student
- trip
- notification
- domain_events
- audit
- or any other schema present in the context.

Rules:

1. Use only tables and columns present
   in the supplied database context.

2. Never invent identifiers.

3. Use schema-qualified table names whenever
   there could be ambiguity.

4. Understand relationships using foreign keys
   and schema context.

5. For read requests generate SELECT/WITH.

6. For write requests you MAY generate:
   INSERT
   UPDATE
   DELETE
   MERGE

7. For DDL requests you MAY generate:
   CREATE
   ALTER
   DROP
   TRUNCATE

8. DML and DDL SQL is REVIEW ONLY.
   The application will never execute it.

9. Generate exactly ONE statement.

10. Never generate SELECT INTO.

11. Never generate transaction commands.

12. For result queries use LIMIT 500 unless
    the user explicitly requests a smaller limit.

13. Return ONLY SQL.

14. Do not return markdown fences.

DATABASE CONTEXT:

{context}
""",
        ),
        (
            "human",
            "{question}",
        ),
    ]
)


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

    sql = response.content.strip()

    if sql.startswith("```"):

        sql = (
            sql
            .replace("```sql", "")
            .replace("```", "")
            .strip()
        )

    if not sql:

        raise ValueError(
            "LLM returned empty SQL."
        )

    operation = classify_sql(sql)

    return QueryResponse(
        sql=sql,
        operation=operation,
    )