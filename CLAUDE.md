# CLAUDE.md

Streamlit app ("RAG DB Copilot") that turns natural-language questions into PostgreSQL SQL using schema-aware RAG, then executes read-only queries and shows results.

## Run

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill DB creds + provider API keys
streamlit run app.py
```

No tests, linter, or build step exist. Only PostgreSQL is supported (`postgresql+psycopg`).

## Environment (.env)

- `DEV_DB_*` / `TEST_DB_*`: HOST, PORT, NAME, USERNAME, PASSWORD (`*_SCHEMA` is in `.env.example` but never read).
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OLLAMA_BASE_URL`.
- `FLYWAY_MIGRATION_PATH`: optional, read in `database/database_inspector.py`. Missing from `.env.example`.
- Never print or commit `.env`.

## Request flow

1. "Discover & index database" -> `inspect_database()` (`database/database_inspector.py`): one `pg_catalog` query for columns, comments and relation kind (table/view/materialized view/foreign table; only objects the user can SELECT), plus PKs, FKs, indexes, `pg_enum` labels, and `common_values` for low-cardinality text/boolean columns from `pg_stats` (no table scans; needs ANALYZE).
2. If `FLYWAY_MIGRATION_PATH` is set, `flyway/flyway_inspector.py` marks tables ACTIVE or DROPPED (regex on CREATE/DROP/ALTER TABLE). `infer_relationships()` adds guessed `<entity>_id` -> `<entity>.id` FKs.
3. The raw metadata is kept in `st.session_state.raw_metadata`. `config/privacy.py: apply_privacy()` produces the filtered metadata and an `AccessPolicy`; `to_documents()` makes one `Document` per active table; these go into an in-memory FAISS index (`rag/schema_indexer.py`). Changing privacy settings rebuilds the index from raw metadata without touching the DB.
4. On a question (`answer_question()` in `app.py`):
   - Retrieval (`rag/retriever.py`): each table is embedded as a short readable `search_text` (name, columns, values), not the full text. Candidates = embedding similarity fused (RRF) with keyword matches on table/column/value words (`rag/textutil.py` handles plurals), with derived/historical tables (aggregated, log, template, export, compiled, historical...) down-weighted unless the question mentions them. Partition children and infrastructure tables (flyway, quartz, shedlock, pg_stat_*) are not indexed.
   - Smart table selection (`services/table_selector.py`, on by default): the model sees a shortlist of one-line summaries (columns, sample values, row counts) and picks the needed tables; FK neighbours are added. Falls back to top-k on failure. Full table text (`metadata["content"]`) is then trimmed by the Context-size profile in `rag/context_builder.py`.
   - Business glossary (`services/glossary.py`, `data/glossary.txt`): `term: meaning` lines expand the retrieval query and are included in the SQL and selection prompts.
   - Approved examples (`services/example_store.py`): the closest verified question/SQL pairs for the datasource (cosine on embeddings, threshold 0.55) are added to the prompt.
   - `process_question()` (`services/query_service.py`) calls the LLM with chat history. Output may be SQL, `CLARIFY: <question>` (shown as a chat question, `NeedsClarification`) or `CANNOT_GENERATE_SQL` (`NoSQLGenerated`).
   - Non-READ SQL is shown but never executed. READ goes through `validate_sql(sql, policy)`, `execute_query()`, and one automatic repair attempt on error (`repair_question`).
   - Optional result summary (`summarize_result`, off by default because it sends the first 20 rows to the model).
5. `render_message()` shows the answer: summary, table/chart tabs (`services/charting.py`), SQL expander, CSV download, "Correct" button (saves the example to `data/approved_queries.json`), token usage and tables considered.

## Layout

- `app.py`: Streamlit UI (sidebar, Chat tab, Database Explorer tab) and orchestration (`answer_question`).
- `config/`: `datasource_config.py` (DEV/Test from env), `model_config.py` (providers, fallback model lists, context profiles), `privacy.py` (exclusions, `AccessPolicy`). `settings.py` is empty.
- `database/`: connection pool, inspector, metadata dataclasses, SQL guard, executor.
- `flyway/`: migration parsing and object lifecycle state.
- `rag/`: embeddings (configurable), FAISS index, hybrid retriever, context builder. `vector_store.py` is empty.
- `services/`: `llm_service.py` (provider factory), `model_catalog.py` (live model lists), `query_service.py`, `table_selector.py`, `glossary.py`, `example_store.py`, `charting.py`.
- `prompts/sql_prompt.py`: SQL system prompt and result-summary prompt.
- `models/query_result.py`: `QueryResult` dataclass. `models/datasource.py` is empty.

## Providers

Ollama, OpenAI, Anthropic, Google, and "OpenAI-compatible" (any base URL). API key and base URL can be entered in the sidebar (kept in session only) or come from env (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`, `OLLAMA_BASE_URL`). The model list is fetched live per provider (`model_catalog.py`) with the static list as fallback and a "Custom..." option. Embedding model: `EMBEDDING_MODEL` env or the Advanced expander (re-index after changing).

## Dead code

`services/sql_generation_service.py` is an unused older prompt plus `generate_sql()`. It is safe to delete. Empty placeholders: `config/settings.py`, `rag/vector_store.py`, `models/datasource.py`.

## Quality and safety design

- Guard (`database/sql_guard.py`): walks the whole tree, blocks nested writes (`WITH x AS (DELETE ...)`), a function denylist (`pg_sleep`, `dblink`, `lo_*`, `pg_read_file`, ...), and, via `AccessPolicy`, excluded tables/columns and `SELECT *` on tables with hidden columns.
- Privacy (`config/privacy.py`): users can exclude schemas/tables and hide columns by pattern (defaults: password, secret, token, ssn, api_key). Excluded objects are removed from the index and enforced in the guard. Sample values can be turned off. Sample values and summaries are sent to the LLM, so review these before using cloud providers.
- Executor: `SET TRANSACTION READ ONLY` plus a 30s `statement_timeout`. Still connect with a read-only DB role.
- Learning loop: only user-approved SQL is stored (question + SQL, no data) in `data/approved_queries.json` (git-ignored; override with `EXAMPLES_PATH`).

## Remaining weaknesses

- The index is rebuilt on every click and not persisted. Default embedding model is small (MiniLM).
- Value hints rely on `pg_stats`, so never-analyzed tables have none; comments are only as good as the schema's documentation.
- Relationship inference is only same-schema, singular `x_id` -> `x.id`. Flyway parsing is regex-based (misses renames, multi-table drops, dollar-quoted bodies).
- `text(sql)` treats `:name` as a bind parameter.
- Column-level privacy is name-based and best-effort (aliases and expressions can still surface values). Pair it with DB permissions.
- No cost estimate (token counts only) and no `EXPLAIN` cost check before running queries.
- No tests.

## Conventions

- Existing style: heavily vertical formatting with one argument per line, section banner comments, dataclasses for models, and no type hints on `app.py`. Match it when editing nearby code.
- Keep the app schema-agnostic. Do not add table names or domain examples to prompts.
- DML/DDL must stay review-only. Never execute non-READ SQL.

## Git state

Three commits on the default branch. `flyway/*`, `prompts/sql_prompt.py`, `database/database_inspector.py`, `database/query_executor.py` and `database/schema_metadata.py` have uncommitted changes. `.gitignore` also ignores `*.faiss` and `*.pkl` (persisted indexes), and `.venv`, `.idea` and `.env`.
