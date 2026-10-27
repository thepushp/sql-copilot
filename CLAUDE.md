# CLAUDE.md

Streamlit app ("RAG DB Copilot") that turns natural-language questions into SQL using schema-aware RAG, then executes read-only queries and shows results. Supports PostgreSQL, MySQL/MariaDB, SQL Server, SQLite and Oracle.

## Run

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill DB creds + provider API keys
streamlit run app.py
```

No linter or build step exists. Tests: `pip install -r requirements-dev.txt && pytest` (see Tests below).

## UI structure

`app.py` `main()`: `render_sidebar()` (setup, model, glossary, privacy, advanced), then `render_header()` — a `st.segmented_control` (`key="nav"`) choosing **Chat / Explorer / ER diagram** plus connection status — then only the active section is built (`render_chat_view`, `render_explorer`, `render_er_diagram`). Consequences: the chat input exists only in the Chat section (so it never covers the diagram), and the ER viewer is not rebuilt on every chat message. Toolbars are `st.container(horizontal=True, wrap=True)` rows of content-width widgets so they wrap instead of clipping; the ER toolbar has two labelled `st.popover` dropdowns whose buttons show the current choice (`Columns: Keys only`, `View: Interactive`; plain `st.radio` with captions inside, plus the inferred-relationships and (whole-schema) hide-unconnected switches), and **Export** lives in the viewer's own bar. The ER canvas is a keyed container (`st.container(key="er_canvas")`) whose iframe height is set by a small injected `<style>` (`max(420px, calc(100vh - 250px))` for diagrams over 4 tables). `BASE_CSS` adjusts page padding and shows the app name (`<div class="sqlcopilot-brand">`, a plain `st.html` element so Streamlit's width wrappers cannot stretch it over the toolbar) fixed in the top strip only while the sidebar is collapsed, and hides the Deploy button with `[data-testid="stAppDeployButton"]` while keeping the three-dot menu (`toolbarMode = "developer"` in `.streamlit/config.toml`; `minimal` removes the menu and `viewer` removes Rerun) (`.stApp:has([data-testid="stSidebar"][aria-expanded="false"])`). The ER diagram state is mirrored in the page URL (`ui/share.py`: `?view=er&mode=table&table=...&depth=2` or `mode=schema&schema=...`), so the address bar is the shareable link; `main()` reads it once per session and `render_er_diagram` applies it (validated against the metadata) after indexing, and the params are cleared when leaving the diagram. The viewer also has a table search (`/` focuses it), schema colour stripes, a legend and a minimap. The viewer adopts the Streamlit theme through `st.context.theme.type` (falls back to the OS setting) and opens centred on the chosen table at no less than 65% scale ("Fit" gives the overview).

## Explorer

`ui/explorer.py` (called from `app.py` as `render_explorer(settings)`; shared bits such as `show_html`, `current_theme` and the `NAV_*` names live in `ui/common.py`). Left: search box + schema picker + a scrollable list of buttons (`nav_<schema.table>`); a non-empty search replaces the schema list with matches across all schemas (`services/schema_search.py`, ranked by name/column/comment/known-value, every word must match, plurals match). Right: a table page whose four sections are chosen with `st.segmented_control(key="table_section")` and only the active one runs (never `st.tabs`, which would run all four every rerun).

- **Properties** uses the discovered metadata plus `services/table_info.py` (`outgoing`, `incoming`, `column_targets`); relationship buttons call `select_table`, which also clears the per-table state in `_PAGE_STATE`.
- **Data** builds one page with `services/sql_builders.page_query` (explicit columns, never `SELECT *`; primary-key order so pages do not overlap; asks for `page_size + 1` rows to know whether a next page exists; dialect-correct LIMIT/TOP/FETCH and OFFSET; native NULL ordering so `ORDER BY` stays index-friendly). The filter box is a SQL condition: `parse_filter` parses it with sqlglot, rejects statements and multiple statements, and places it only inside the WHERE of a query built by us; every query then goes through `validate_sql(sql, policy, dialect)` (`_checked_run`), so excluded tables, hidden columns, system catalogs and dangerous functions are refused. The last result is kept in `_data_cache` so unrelated reruns do not hit the database; the row count is on demand (`COUNT(*)`), otherwise the estimate is shown. Export (`services/exports.py`) re-runs the same query with up to 10,000 rows and offers CSV / JSON / Excel; it converts values drivers return that pandas cannot write (UUID, arrays, JSON, IP addresses, bytes, time zones, decimals). Browsing and exporting are audited (`browse`, `export` events with the SQL).
- **Diagram** reuses the interactive viewer (`graph_data` + `viewer_html`) around the table, 1 or 2 steps; "Open in the ER diagram section" sets `nav`, `er_mode` and `er_table` in session state.
- **SQL** uses `services/sql_templates.py`: runnable reads (select, count, joins to parents, rows of tables that point here, count per column) and review-only INSERT/UPDATE/DELETE/CREATE TABLE/CREATE INDEX with `:name` placeholders. The "Run" button exists only for runnable templates and goes through the same checked path. DDL is rebuilt from metadata (inferred relationships are not written as constraints); view definitions are not available yet.
- Not done on purpose: editing data and running DDL (the app stays read-only). Not yet: view definitions, triggers/constraints/sizes, a free-form SQL editor, query plans (see the roadmap discussion).

`services/sql_builders.py` is the single place that quotes identifiers and builds SQL for all dialects (also used by `services/example_questions.py`).

## Datasources and dialects

Two sources: `.env` (DEV_DB_*/TEST_DB_*, always available) and the sidebar **Add a connection** form (database type, host, port, database, username, password, SSL mode for PostgreSQL, Test button). UI connections live only in `st.session_state.custom_datasources` for that browser session; passwords are never written to disk and are not shared between sessions. The resolved config is passed explicitly (`inspect_database(name, config)`, `execute_query(name, sql, config)`); engines are cached by name plus credentials in `database/connection_manager.py`.

`database/dialects.py` holds one `Dialect` profile per database (`postgresql`, `mysql`, `mssql`, `sqlite`, `oracle`): SQLAlchemy URL scheme/driver (psycopg, pymysql, pymssql, oracledb, sqlite), default port, sqlglot dialect, system schemas, read-only/timeout setup statements run before every query, forbidden functions, row-limit hint and prompt notes. To add a database, add a profile there (and a driver to `requirements.txt`). The `driver` key in a datasource config (`DEV_DB_DRIVER`, `TEST_DB_DRIVER` in env) selects it; default is postgresql. For SQLite the "database" is a file path opened read-only; for Oracle it is the service name.

Metadata: PostgreSQL uses the rich `pg_catalog` inspector (`_inspect_postgres`: comments, enum labels, `common_values` from `pg_stats`, materialized views, row estimates, partitions skipped). Other dialects use `database/generic_inspector.py` (SQLAlchemy reflection: tables, views, columns, PKs, FKs, indexes, comments where supported) with no sample values or row estimates. `inspect_database` dispatches, then applies Flyway state and relationship inference for all dialects.

The SQL guard (`validate_sql(sql, policy, dialect_key)`), prompt (`{dialect_name}`, `{limit_hint}`, `{dialect_notes}`) and executor are dialect-aware. SQL Server has no read-only transaction, so a SELECT-only login is required there (the UI shows a warning).

Tested against live servers: PostgreSQL and SQLite only. MySQL, SQL Server and Oracle are implemented from SQLAlchemy/sqlglot behavior and unit-checked (URLs, drivers load, guard matrix) but not run against real servers.

## Environment (.env)

- `DEV_DB_*` / `TEST_DB_*`: HOST, PORT, NAME, USERNAME, PASSWORD (`*_SCHEMA` is in `.env.example` but never read).
- `DEV_DB_DRIVER` / `TEST_DB_DRIVER`: postgresql (default), mysql, mssql, sqlite, oracle.
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
   - `process_question()` (`services/query_service.py`) calls the LLM with chat history. With `on_token` it streams (`_call`: `llm.stream`, chunks merged so token usage still works, falls back to `invoke`); `ui/progress.py` `Progress` wraps the chat `st.status` so existing `status.update(label=...)` calls become a checklist of timed steps and the SQL appears live; the box collapses to `Done · Ns` at the end. Output may be SQL, `CLARIFY: <question>` (shown as a chat question, `NeedsClarification`) or `CANNOT_GENERATE_SQL` (`NoSQLGenerated`).
   - Non-READ SQL is shown but never executed. READ goes through `validate_sql(sql, policy)`, `execute_query()`, and one automatic repair attempt on error (`repair_question`).
   - Optional result summary (`summarize_result`, off by default because it sends the first 20 rows to the model).
5. Landing-page examples (`services/example_questions.py`): after indexing (and after privacy changes) the app builds candidate questions with SQL from the filtered metadata (row count, group-by on a column with known values, FK join with the parent's label column, most recent rows, first rows), builds the SQL with sqlglot for the active dialect, runs each through the safety guard and the database, and keeps up to four that really return data (one per kind first). Tables over 2M estimated rows are not scanned. Clicking one runs its verified SQL directly (`run_example`), so it needs no model; the answer is flagged `example` and joins the chat history.
6. ER Diagram tab: `services/er_diagram.py` selects tables (`expand_around`, `schema_tables`) and builds Graphviz DOT (static view, `st.graphviz_chart`, no system install) and Mermaid export from the filtered metadata. The default **Interactive** view is `services/er_interactive.py` (`graph_data` -> JSON, `viewer_html`) plus dependency-free assets in `services/assets/er_viewer.{html,css,js}`, embedded with `st.iframe` (falls back to `components.v1.html`). Layout puts referenced tables left of referencing ones; clicking a column, relationship line or table header highlights the related edges, columns and tables and dims the rest, with a description panel. Table/column names are untrusted: the data goes in as escaped JSON inside `<script type="application/json">` and the JS only uses `textContent`/`setAttribute` (never innerHTML) because the iframe is same-origin with the app. The layout/selection functions are pure and exported for Node tests. **Export** (PNG, SVG, Mermaid, DOT) is done in the browser inside the viewer (the frame's sandbox allows downloads): `standaloneSvg()` clones the SVG, inlines every computed style so it renders anywhere, drops hit areas and tooltips and adds a background; PNG is that SVG drawn on a canvas at 2x (capped at 8000px / 40M pixels). It exports the whole diagram in its current state, highlight included. Mermaid/DOT text is passed in as `data.exports`. Modes: around a table (BFS by relationship depth), whole schema (best-connected first, capped at 40 tables), or the tables used by the last answer. Inferred relationships are dashed.
6. `render_message()` shows the answer: summary, table/chart tabs (`ui/results.py`: sortable grid with number/date formatting, row filter from 8 rows, truncation note at 500 rows, chart picker shared with the Explorer; `services/charting.py`), SQL expander, CSV download, "Correct" button (saves the example to `data/approved_queries.json`), token usage and tables considered.

## Layout

- `app.py`: Streamlit UI (sidebar, Chat tab, Database Explorer tab) and orchestration (`answer_question`).
- `config/`: `datasource_config.py` (DEV/Test from env), `model_config.py` (providers, fallback model lists, context profiles), `privacy.py` (exclusions, `AccessPolicy`). `settings.py` is empty.
- `database/`: `dialects.py` (per-database profiles and URL builder), connection pool, `database_inspector.py` (PostgreSQL + dispatch), `generic_inspector.py` (other databases), metadata dataclasses, SQL guard, executor.
- `flyway/`: migration parsing and object lifecycle state.
- `rag/`: embeddings (configurable), FAISS index, hybrid retriever, context builder. `vector_store.py` is empty.
- `ui/`: `common.py` (shared helpers), `explorer.py` (the Explorer).
- `services/`: `sql_builders.py`, `sql_templates.py`, `schema_search.py`, `table_info.py`, `exports.py`, `data_profile.py`, `example_questions.py`, `er_diagram.py`, `llm_service.py` (provider factory), `model_catalog.py` (live model lists), `query_service.py`, `table_selector.py`, `glossary.py`, `example_store.py`, `charting.py`.
- `prompts/sql_prompt.py`: SQL system prompt and result-summary prompt.
- `models/query_result.py`: `QueryResult` dataclass. `models/datasource.py` is empty.

## Providers

Ollama, OpenAI, Anthropic, Google, and "OpenAI-compatible" (any base URL). API key and base URL can be entered in the sidebar (kept in session only) or come from env (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`, `OLLAMA_BASE_URL`). The model list is fetched live per provider (`model_catalog.py`) with the static list as fallback and a "Custom..." option. Embedding model: `EMBEDDING_MODEL` env or the Advanced expander (re-index after changing).

## Answer quality (checks, plan, evaluation)

- `services/query_checks.py`: `review(sql, rows, dialect, metadata)` returns `Issue`s: cartesian join, SUM/AVG of a parent column joined to its child (fan-out), all-NULL columns, and, for empty results, string filters that match nothing (separator/case-insensitive match against known values; text columns with no known values are flagged too). `find_stored_values` then runs one small DISTINCT ... LIKE read per issue (through the guard with the privacy policy) so the retry sees how the value is really stored (`trf manage` vs `trf.manage`). `estimate_plan` runs `EXPLAIN` (PostgreSQL, MySQL) without executing and warns on big index-less scans. All of it warns; only the guard blocks.
- `app.py` `_answer_question`: an empty result with fixable issues triggers one `refine_question` retry; the retry replaces the answer only if it returns rows. `annotate()` fills `plan`, `warnings`, `estimate` on the message.
- `services/sql_explain.py` (`describe_sql`): one-line plain-English plan from the parsed SQL (no model call). `services/followups.py`: rule-based follow-up chips for the last answer.
- Answers show an editable SQL box; `run_edited_sql` re-runs it through `classify_sql` + `validate_sql` + the executor (audit event `edit`).
- `infer_shared_key_relationships` links tables that carry the same unique identifier column (`sub`, or `<prefix>_sub`) with the same type; marked inferred.
- Evaluation: `python -m evals.run --datasource NAME --cases evals/cases.example.json --provider P --model M` scores execution accuracy (`services/evaluation.py`; compares result sets, ignoring column names/order and row order unless `ordered`). Write your own cases file against your schema.

## Dead code

Empty placeholders (safe to delete): `config/settings.py`, `rag/vector_store.py`, `models/datasource.py`.

## Quality and safety design

- Guard (`database/sql_guard.py`): walks the whole tree, blocks nested writes (`WITH x AS (DELETE ...)`), a function denylist (`pg_sleep`, `dblink`, `lo_*`, `pg_read_file`, ...), and, via `AccessPolicy`, excluded tables/columns and `SELECT *` on tables with hidden columns.
- Privacy (`config/privacy.py`): users can exclude schemas/tables and hide columns by pattern (defaults: password, secret, token, ssn, api_key). Excluded objects are removed from the index and enforced in the guard. Sample values can be turned off. Sample values and summaries are sent to the LLM, so review these before using cloud providers.
- System objects: `validate_sql` rejects reads of system catalogs (`pg_*`, `information_schema`, `sqlite_*`, `dba_*`, `sys`, ...) unless `SQL_COPILOT_ALLOW_SYSTEM_CATALOGS` is set, and function prefixes such as `pg_advisory*`, `lo_*`, `xp_*`, `sp_*`, `dbms_*`. Both come from fields on `Dialect`.
- Executor: results are streamed (`stream_results=True`) and only 500 rows are fetched, so a huge result does not load into memory. It runs the dialect's setup statements first (PostgreSQL/Oracle/MySQL read-only transaction, SQLite `query_only` on a read-only file, statement timeouts where supported). Still connect with a read-only DB role.
- Learning loop: only user-approved SQL is stored (question + SQL, no data) in `data/approved_queries.json` (git-ignored; override with `EXAMPLES_PATH`).

## Remaining weaknesses

- The index is rebuilt on every click and not persisted. Default embedding model is small (MiniLM).
- Value hints rely on `pg_stats`, so never-analyzed tables have none; comments are only as good as the schema's documentation.
- Relationship inference is only same-schema, singular `x_id` -> `x.id`. Flyway parsing is regex-based (misses renames, multi-table drops, dollar-quoted bodies).
- `text(sql)` treats `:name` as a bind parameter.
- Column-level privacy is name-based and best-effort (aliases and expressions can still surface values). Pair it with DB permissions.
- No cost estimate (token counts only) and no `EXPLAIN` cost check before running queries.
- No answer-quality evaluation set: the tests cover code paths (96% line coverage), not how accurate the model's SQL is.
- MySQL, SQL Server and Oracle are not tested against live servers. No database-side query timeout for Oracle and SQLite; SQL Server has no read-only transaction.
- Glossary and approved examples are server-wide (`data/`), not per user; examples are matched by datasource name only.
- Database engines are cached per credential and never disposed.

## Logging, audit and packaging

- `services/audit.py`: `configure_logging()` (idempotent, called at app start) sets up console logging under the `sql_copilot` logger and a rotating JSON-lines audit file (`data/audit.log`, override with `SQL_COPILOT_AUDIT_LOG`, `off` disables). `audit(event, **fields)` never raises, drops secret-named fields and None values, and truncates long text. Events: `index`, `answer` (outcome ok/blocked/rejected/error/no_sql/clarify, tokens, timing, tables), `example`, `approve`, `connection` (never passwords), `privacy`. Result rows are never logged. Previously silent `except` blocks now log warnings.
- `tests/conftest.py` points the audit file at a temp path for every test (autouse); the shared `env` fixture (SQLite-only datasources, fake embeddings, isolated glossary/examples) lives there too.
- Packaging: `README.md`, `requirements.lock` (exact tested versions, Python 3.14; regenerate with the command in its header), `requirements-dev.txt`, `Dockerfile` (lint-checked only, not yet built), `.github/workflows/ci.yml`, `.streamlit/config.toml` (binds 127.0.0.1, telemetry off).
- Design stance: no built-in authentication (self-hosted tool). The README tells people to put their own proxy/VPN in front if they share it.

## Conventions

- Existing style: heavily vertical formatting with one argument per line, section banner comments, dataclasses for models, and no type hints on `app.py`. Match it when editing nearby code.
- Keep the app schema-agnostic. Do not add table names or domain examples to prompts.
- DML/DDL must stay review-only. Never execute non-READ SQL.

## Tests

`pytest` (config in `pytest.ini`, tests in `tests/`) runs in about 15s with no network, LLM, embedding download or PostgreSQL needed.

- Unit tests per module: SQL guard (all dialects + privacy policy), dialects/URLs, metadata and relationship inference, privacy, Flyway, retrieval/ranking, query service, table selector, glossary, example store, charting, ER diagram, providers (HTTP and LLM factories mocked).
- Database layer runs against a temporary SQLite file (`shop_db` fixture in `tests/conftest.py`), including DB-level read-only enforcement.
- `tests/test_app.py` drives the real `app.py` through Streamlit's `AppTest` with a scripted stand-in model (`ScriptedLLM`), fake embeddings and SQLite as the only datasource, covering indexing, answering, repair, clarification, approval/reuse, glossary, privacy, the connection form and the ER tab. They patch `services.llm_service.get_llm`, `rag.embeddings.get_embeddings` and `DATASOURCES`, so they never touch a real database.
- Explorer: unit tests for `sql_builders`, `sql_templates`, `schema_search`, `table_info`, `exports`/`data_profile`; `tests/test_explorer.py` drives the real Explorer (navigator, search, properties, data, export, diagram, SQL, privacy, edge cases) on a SQLite database with 125 orders; `tests/test_explorer_integration.py` (opt in with `SQL_COPILOT_PG_TEST=1`) runs paging, filters, every runnable template and the exports on real PostgreSQL data.
- ER viewer: `tests/test_er_interactive.py` (data + escaping), `tests/js/er_viewer.test.js` run by `tests/test_er_viewer_logic.py` via `node --test` (layout, selection), and `tests/test_er_viewer_browser.py` (real clicks in Chrome through Playwright, including hostile table names; skipped if Playwright/Chrome are missing).
- Optional PostgreSQL integration tests: `SQL_COPILOT_PG_TEST=1 pytest -m integration` (read-only against the first configured datasource).
- MySQL, SQL Server and Oracle have URL, driver and guard tests only; no live-server tests.

## Git state

Three commits on the default branch. `flyway/*`, `prompts/sql_prompt.py`, `database/database_inspector.py`, `database/query_executor.py` and `database/schema_metadata.py` have uncommitted changes. `.gitignore` also ignores `*.faiss` and `*.pkl` (persisted indexes), and `.venv`, `.idea` and `.env`.
