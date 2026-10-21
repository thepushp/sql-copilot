# SQL Copilot

Ask questions about your database in plain English and get SQL, results, charts and ER diagrams back. A Streamlit app that grounds a language model in your real schema, then runs only read-only queries.

- Works with **PostgreSQL, SQLite, MySQL/MariaDB, SQL Server and Oracle**
- Works with **Ollama (fully local), OpenAI, Anthropic, Google, or any OpenAI-compatible server**
- Self-hosted: your data stays between your machine, your database and the model provider you choose

## Features

- **Schema-aware answers:** tables, columns, keys, comments, views and sample values are indexed; a hybrid (embedding + keyword) search plus a model-driven table selection picks what the model sees
- **Safe by design:** one read-only statement only; writes, locks, dangerous functions and system catalogs are blocked; queries run in a read-only transaction with a timeout and a 500-row cap
- **Verified example questions:** the landing page offers questions built from your schema and checked against your database, so they always work and need no model
- **Conversation:** follow-ups, clarifying questions, automatic repair of failing queries, and a 👍 button that teaches the app your verified answers
- **Business glossary:** define your own terms (`bus: the vehicle table`)
- **Privacy controls:** exclude schemas/tables, hide columns by pattern, turn off sample values
- **Results:** table, chart, CSV download, optional plain-language summary
- **Clean, compact interface:** a slim header with Chat / Explorer / ER diagram sections (the app name moves to the top strip when the sidebar is collapsed), toolbars that wrap on narrow windows, light and dark theme support, and a diagram canvas that uses the rest of the window
- **Explorer:** a navigator with search across every schema (table names, column names, comments, known values) next to a table page with four sections:
  - **Properties:** columns, types, keys and what they point to, indexes, and clickable "references" and "referenced by" lists to move between related tables
  - **Data:** a paged, sortable, filterable view of the rows with column statistics, a row count on demand, and export to CSV, JSON or Excel (up to 10,000 rows)
  - **Diagram:** the table and its neighbours, with a shortcut to the full ER diagram
  - **SQL:** ready-made SQL for the table (select, count, group, joins through relationships) that you can run, plus insert / update / delete / CREATE TABLE statements for review only
- **Interactive ER diagrams:** around a table, a whole schema, or the tables behind your last answer. Click a column, a relationship line or a table header to highlight how it connects to other tables (click a primary key to see every table that references it). The Columns and View dropdowns say what they control and what is currently chosen. **Export** saves a PNG image (twice the on-screen size, whole diagram), an SVG, or Mermaid / Graphviz text; image exports match what you see, including any highlight and the light or dark theme
- **Audit log:** a local record of what was asked and what SQL ran (never the data)

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock        # exact tested versions (or requirements.txt for latest)
cp .env.example .env                    # optional: DB credentials and API keys
streamlit run app.py
```

Open http://127.0.0.1:8501, choose a datasource in the sidebar (or **Add a connection**), click **Discover & index database**, and ask a question. The first run downloads a small embedding model (~90 MB).

The app was developed and tested on Python 3.14.

## Configuration

Connect either in the sidebar (kept in your browser session only, never written to disk) or through `.env`:

| Setting | Meaning |
|---|---|
| `DEV_DB_*`, `TEST_DB_*` | `DRIVER` (`postgresql`, `mysql`, `mssql`, `sqlite`, `oracle`), `HOST`, `PORT`, `NAME`, `USERNAME`, `PASSWORD`. For SQLite `NAME` is the file path; for Oracle it is the service name |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`, `OLLAMA_BASE_URL` | Model providers (or type them in the sidebar) |
| `EMBEDDING_MODEL` | Sentence-transformers model used for schema search |
| `FLYWAY_MIGRATION_PATH` | Folder of Flyway `*.sql` migrations; tables they drop are ignored |
| `SQL_COPILOT_AUDIT_LOG` | Audit file path, or `off` (default `data/audit.log`) |
| `SQL_COPILOT_LOG_LEVEL` | Console log level (default `INFO`) |
| `SQL_COPILOT_ALLOW_SYSTEM_CATALOGS` | Set to allow reads of `pg_catalog`, `information_schema`, etc. |

## Supported databases

| Database | Status |
|---|---|
| PostgreSQL | Tested against a live server (richest metadata: comments, enums, sample values, row estimates) |
| SQLite | Tested end to end (file is opened read-only) |
| MySQL / MariaDB, SQL Server, Oracle | Implemented and unit-tested (connection URLs, SQL safety checks, drivers load) but **not yet run against live servers**. Reports and fixes welcome |

## Safety and privacy: read this before pointing it at real data

**What protects your database**

1. Only a single `SELECT`/`WITH` statement is allowed. Inserts, updates, DDL, locking reads (`FOR UPDATE`), dangerous functions (`pg_sleep`, advisory locks, `dblink`, `xp_cmdshell`, ...) and system catalogs are rejected, including when hidden inside a CTE or subquery.
2. Protection inside the database differs by engine:

   | | Read-only enforced by the database | 30 s query timeout |
   |---|---|---|
   | PostgreSQL | yes (read-only transaction) | yes |
   | MySQL / MariaDB | yes (read-only transaction) | yes |
   | Oracle | yes (read-only transaction) | no |
   | SQLite | yes (file opened read-only) | no |
   | SQL Server | **no** (the SQL checker and your login's permissions are the only barrier) | lock timeout only |

3. Chat answers return at most 500 rows and large results are streamed, so a huge result does not fill memory. The Explorer's data view fetches one page at a time with the same safety check and privacy rules; hidden columns and excluded tables never appear in it or in its exports.

These are safeguards, not guarantees. **Always connect with a database user that only has `SELECT` permission**, ideally on a replica.

**What leaves your machine** (when using a cloud model provider)

- Your questions and recent chat history
- Table and column names, types, comments, and up to 10 common values for low-cardinality text columns (PostgreSQL)
- Your glossary and approved example queries
- With *Summarize results* turned on: the first 20 result rows

Use Ollama or another local model to keep everything on your machine. Use the **Privacy** panel to exclude tables, hide columns (defaults hide `*password*`, `*token*`, `*secret*`, `*ssn*`, `*api_key*`) and turn off sample values.

**No built-in login.** This is a personal/local tool. It listens on `127.0.0.1` only by default. Anyone who can open the page can query the connected database with its credentials and can point the server at any host or file. If you share it, put your own authentication (a reverse proxy with SSO, a VPN) in front, and do not expose it to the internet.

**Audit log.** Every question, generated SQL, outcome, row count, timing and token count is appended to `data/audit.log` (JSON lines, rotated at 5 MB). Result data, passwords and API keys are never written. Set `SQL_COPILOT_AUDIT_LOG=off` to disable it.

## Docker

```bash
docker build -t sql-copilot .
docker run --rm -p 127.0.0.1:8501:8501 --env-file .env \
  -v sql-copilot-data:/app/data -v sql-copilot-models:/home/app/.cache sql-copilot
```

Publish the port on `127.0.0.1` as shown. Inside the container the app must listen on all interfaces, so `-p 8501:8501` would expose it to your network. To reach a database or Ollama on your host, use `host.docker.internal` as the host. The Dockerfile has not been built in CI yet.

## Development

```bash
pip install -r requirements-dev.txt
pytest                                   # ~30 s, no network, model or database needed
SQL_COPILOT_PG_TEST=1 pytest -m integration   # optional: read-only checks against your PostgreSQL
```

The UI tests drive the real app with a scripted stand-in model, fake embeddings and a temporary SQLite database. The ER viewer's JavaScript is tested with Node (`node --test`), and its click behavior with Playwright in your installed Chrome; both are skipped automatically if Node, Playwright or Chrome is missing. See `CLAUDE.md` for the architecture and design notes.

## Known limitations

- Answer quality depends on the model and on how clearly your schema is named; there is no benchmark suite yet. Use the glossary for ambiguous terms.
- Single-process Streamlit app: fine for personal or small-team use, not built for many concurrent users.
- The glossary and approved examples are stored per server (`data/`), not per user.
- Non-PostgreSQL databases get less metadata (no sample values or row estimates).

## License

[MIT](LICENSE) © 2026 Pushpraj Priy
