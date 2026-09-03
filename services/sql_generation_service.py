from langchain_core.prompts import (
    ChatPromptTemplate,
)


SQL_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are a database SQL assistant.

Convert the user's request into ONE
PostgreSQL read-only SQL query.

Rules:

1. Use only tables and columns present
   in the supplied schema.

2. Never invent identifiers.

3. Generate only SELECT or WITH queries.

4. Never generate:
   INSERT
   UPDATE
   DELETE
   MERGE
   TRUNCATE
   CREATE
   ALTER
   DROP
   GRANT
   REVOKE
   COPY
   DO
   CALL

5. Never generate multiple statements.

6. Never use SELECT INTO.

7. If the schema context is insufficient,
   return exactly:

   CANNOT_GENERATE_SQL

8. Prefer explicit column names when practical.

9. Limit result-oriented queries to
   500 rows.

10. Return only SQL.

11. Do not use markdown fences.

12. Use PostgreSQL syntax.

Schema context:

{schema_context}
""",
        ),

        (
            "human",
            "{question}",
        ),
    ]
)


def generate_sql(
    llm,
    question,
    schema_docs,
):

    context = "\n\n".join(
        document.page_content
        for document in schema_docs
    )

    messages = SQL_PROMPT.invoke(
        {
            "schema_context": context,
            "question": question,
        }
    )

    response = llm.invoke(
        messages
    )

    sql = response.content.strip()

    if sql.startswith("```"):

        sql = (
            sql
            .replace("```sql", "")
            .replace("```", "")
            .strip()
        )

    if sql == "CANNOT_GENERATE_SQL":

        raise ValueError(
            "I could not generate safe SQL "
            "from the available schema."
        )

    return sql