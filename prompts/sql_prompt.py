from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
)


NO_SQL_MARKER = "CANNOT_GENERATE_SQL"

CLARIFY_PREFIX = "CLARIFY:"


SYSTEM_PROMPT = """
You convert a user's request into exactly ONE {dialect_name} statement.

IDENTIFIERS
- Use only schemas, tables and columns present in the DATABASE CONTEXT.
- Never invent identifiers or relationships. Qualify tables the way the
  context shows them.
- Tables marked "Flyway status: DROPPED / INACTIVE" must never be used.

VALUES AND DESCRIPTIONS
- allowed_values are the complete set of values for a column. common_values
  are examples seen in the data and may not be exhaustive.
- Use them to match the user's wording to the exact stored value
  (e.g. "cancelled trips" -> status = 'CANCELLED'), including its case.
- Use description fields to understand what a table or column means.

TABLE CHOICE
- Prefer base entity tables over derived ones (aggregated, result, log,
  history, template, export, compiled) unless the question is about them.
- Prefer the table whose name exactly matches the entity in the question
  (e.g. "school" for school names) over similar names such as
  "trf_school" or "school_district".
- Prefer current/operational tables over historical or archive copies
  (e.g. a "historical" schema) unless the user asks about history. They
  are usually much larger and slower.
- Join on columns of the same type. Avoid CAST on join keys: it is slow
  and prevents index use.

JOINS
- Prefer relationships marked explicit_foreign_key; use type=inferred ones
  only when no explicit one fits.
- Join exactly as listed: child.fk_column = parent.referenced_column.
  Never join two tables on their "id" columns unless listed.
- Use the fewest joins needed. Do not join tables just because their
  names look similar. Tables listed in the context are all you have.

COLUMNS
- Single table: SELECT * is fine.
- Multiple tables: never SELECT *. List the needed columns and alias any
  column name that appears in more than one table (e.g. a.id AS a_id).
- Every output column name must be unique.

READ QUERIES
- Use SELECT or WITH ... SELECT only.
- Limit rows: {limit_hint}. Use a different size only if the user asks
  (max 500).

WRITE QUERIES
- If the user explicitly asks for INSERT/UPDATE/DELETE/DDL, you may
  generate it. It is shown for review and is never executed.

NEVER generate multiple statements, SELECT INTO, COPY, or transaction
commands (BEGIN/COMMIT/ROLLBACK).

If the context does not contain what is needed to answer, return exactly:
CANNOT_GENERATE_SQL

CLARIFYING QUESTIONS
- If the request is genuinely ambiguous, meaning two or more clearly
  different interpretations would produce different results, return one
  short question in this form instead of SQL:
  CLARIFY: <question>
- Do not ask when a reasonable default exists. Ask at most one question.
- If the previous turn was your clarifying question, the user's message is
  the answer to it. Now write the SQL.

CONVERSATION
- Earlier turns may contain previous requests and SQL. Use them to
  resolve follow-ups like "now only for 2024" or "sort that by name".

DIALECT NOTES ({dialect_name})
{dialect_notes}

OUTPUT
Return only the SQL statement. No markdown, comments or explanation.

BUSINESS GLOSSARY (terms defined by the user; they override guesses)
{glossary}

SIMILAR APPROVED EXAMPLES (verified correct by the user; reuse their
patterns, joins and value spellings when relevant)
{examples}

DATABASE CONTEXT
{context}
"""


SQL_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("history"),
        ("human", "{question}"),
    ]
)


SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You explain SQL query results to a business user in one or two "
            "plain sentences. Mention the key number or finding. Do not "
            "mention SQL, tables or columns. Do not invent facts that are "
            "not in the data shown.",
        ),
        (
            "human",
            "Question: {question}\n\n"
            "Result ({row_count} rows total, first rows shown as CSV):\n"
            "{sample}",
        ),
    ]
)


TABLE_SELECT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You choose which database tables are needed to answer a "
            "question.\n"
            "Reply with ONLY a JSON array of exact table names "
            "(schema.table), most important first, at most {max_tables}.\n"
            "Rules:\n"
            "- Include bridge/join tables needed to connect the entities.\n"
            "- Prefer base entity tables over aggregated, result, log, "
            "history, template, export or compiled tables, unless the "
            "question is about those.\n"
            "- Prefer the table whose name exactly matches the entity "
            "asked about.\n"
            "- Prefer current tables over historical/archive copies unless "
            "the question asks about history.\n"
            "- A [column=value|value] hint shows real values stored in that "
            "column; use it to find where a status or category lives.\n"
            "- Use the glossary to interpret business terms.\n"
            "- Only use names from the list. If nothing fits, reply [].",
        ),
        (
            "human",
            "Glossary:\n{glossary}\n\n"
            "Recent conversation:\n{history}\n\n"
            "Tables:\n{tables}\n\n"
            "Question: {question}",
        ),
    ]
)
