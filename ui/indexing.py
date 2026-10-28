"""Connections, indexing and the landing-page examples."""

import time

from sqlalchemy import text
import streamlit as st

from config.datasource_config import DATASOURCES
from config.privacy import apply_privacy
from database.connection_manager import get_engine
from database.dialects import get_dialect, is_configured
from database.query_executor import execute_query
from database.sql_guard import validate_sql
from rag.schema_indexer import build_schema_index
from services.audit import audit
from services.example_questions import build_candidates, verify_examples
from services.sql_explain import describe_sql
from services.sql_qualify import format_sql
from rag import embeddings
from database import database_inspector
from ui.state import all_datasources, logger


def _connection_from_form():

    dialect_key = st.session_state.get("conn_type", "postgresql")

    dialect = get_dialect(dialect_key)

    config = {
        "driver": dialect_key,
        "host": "",
        "port": None,
        "database": st.session_state.get("conn_db", "").strip(),
        "username": "",
        "password": "",
        "sslmode": None,
    }

    if dialect.needs_host:

        config.update(
            host=st.session_state.get("conn_host", "").strip(),
            port=int(
                st.session_state.get(
                    f"conn_port_{dialect_key}",
                    dialect.default_port,
                )
            ),
            username=st.session_state.get("conn_user", "").strip(),
            password=st.session_state.get("conn_password", ""),
        )

    if dialect.supports_ssl_mode:

        mode = st.session_state.get("conn_ssl", "default")

        config["sslmode"] = None if mode == "default" else mode

    return config


def add_connection():

    name = st.session_state.get("conn_name", "").strip()

    config = _connection_from_form()

    if not name:

        st.session_state.conn_message = ("error", "Enter a connection name.")

        return

    if name in DATASOURCES:

        st.session_state.conn_message = (
            "error",
            f"'{name}' is already defined in the environment. "
            "Choose another name.",
        )

        return

    if not is_configured(config):

        st.session_state.conn_message = (
            "error",
            "Fill in all required connection fields.",
        )

        return

    st.session_state.custom_datasources[name] = config

    st.session_state.datasource_select = name

    st.session_state.conn_password = ""
    st.session_state.conn_name = ""

    audit(
        "connection",
        action="added",
        name=name,
        dialect=config["driver"],
        host=config.get("host"),
        port=config.get("port"),
        database=config.get("database"),
    )

    st.session_state.conn_message = (
        "success",
        f"Added '{name}'. Click Discover & index database.",
    )


def remove_connection(name):

    st.session_state.custom_datasources.pop(name, None)

    audit("connection", action="removed", name=name)

    if st.session_state.indexed_datasource == name:

        st.session_state.indexed_datasource = None
        st.session_state.schema_index = None

    st.session_state.datasource_select = next(iter(DATASOURCES))


def test_connection(config, label):

    if not is_configured(config):

        return "error", "Fill in all required connection fields."

    try:

        with get_engine(label, config).connect() as connection:

            connection.execute(text("SELECT 1"))

    except Exception as exc:

        audit(
            "connection",
            action="test",
            ok=False,
            dialect=config.get("driver"),
            host=config.get("host"),
            error=str(exc).splitlines()[0],
        )

        return "error", f"Connection failed: {str(exc).splitlines()[0][:200]}"

    audit(
        "connection",
        action="test",
        ok=True,
        dialect=config.get("driver"),
        host=config.get("host"),
    )

    return "success", "Connection works."


def refresh_example_questions():
    """Build example questions from the schema and keep only those that work."""

    st.session_state.example_questions = []

    metadata = st.session_state.database_metadata
    name = st.session_state.indexed_datasource

    if not metadata or not name:
        return

    config = all_datasources().get(name)
    dialect_key = get_dialect(config).key
    policy = st.session_state.access_policy

    try:

        with st.spinner("Preparing example questions..."):

            st.session_state.example_questions = verify_examples(
                build_candidates(metadata, dialect_key),
                lambda sql: execute_query(name, sql, config),
                lambda sql: validate_sql(sql, policy, dialect_key).allowed,
            )

    except Exception:

        logger.warning("Could not prepare example questions", exc_info=True)

        st.session_state.example_questions = []


def run_example(example, settings):
    """Run a verified example directly; no model call is needed."""

    message = {
        "role": "assistant",
        "question": example.question,
        "datasource": settings.datasource_name,
        "sql": format_sql(example.sql, settings.dialect_key),
        "tables": example.tables,
        "example": True,
        "dialect": settings.dialect_key,
        "plan": describe_sql(example.sql, settings.dialect_key),
        "input_tokens": 0,
        "output_tokens": 0,
    }

    result = execute_query(
        settings.datasource_name,
        example.sql,
        settings.datasource_config,
    )

    if result.error:

        message["error"] = f"The query failed: {result.error}"

    else:

        message["rows"] = result.rows
        message["row_count"] = result.row_count
        message["content"] = (
            f"**{result.row_count} rows** in {result.execution_ms:.0f} ms"
        )

    audit(
        "example",
        datasource=settings.datasource_name,
        dialect=settings.dialect_key,
        question=example.question,
        sql=example.sql,
        outcome="error" if result.error else "ok",
        rows=result.row_count,
        elapsed_ms=round(result.execution_ms),
        error=result.error,
    )

    return message


class NothingToIndex(ValueError):
    pass


def rebuild_index(embedding_model):

    filtered, policy = apply_privacy(
        st.session_state.raw_metadata,
        st.session_state.privacy,
    )

    documents = filtered.to_documents()

    if not documents:

        raise NothingToIndex(
            "There are no tables to index. Check that this user can read at "
            "least one table, and that the privacy settings do not exclude "
            "everything."
        )

    index = build_schema_index(
        documents,
        embeddings.get_embeddings(embedding_model),
    )

    st.session_state.database_metadata = filtered
    st.session_state.access_policy = policy
    st.session_state.schema_index = index
    st.session_state.index_embedding_model = embedding_model


def index_database(datasource_name, embedding_model, config=None):

    started = time.perf_counter()

    with st.spinner("Discovering schemas and building index..."):

        try:

            raw = database_inspector.inspect_database(datasource_name, config)

            st.session_state.raw_metadata = raw
            st.session_state.indexed_datasource = datasource_name

            rebuild_index(embedding_model)

        except Exception as exc:

            st.session_state.indexed_datasource = None
            st.session_state.schema_index = None

            logger.warning("Indexing %s failed: %s", datasource_name, exc)

            audit(
                "index",
                datasource=datasource_name,
                ok=False,
                error=str(exc),
            )

            st.session_state["index_error"] = {
                "datasource": datasource_name,
                "text": str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__,
            }

            return

    st.session_state.pop("index_error", None)

    st.session_state.messages = []

    refresh_example_questions()

    metadata = st.session_state.database_metadata

    audit(
        "index",
        datasource=datasource_name,
        dialect=get_dialect(config or all_datasources().get(datasource_name)).key,
        ok=True,
        schemas=metadata.schema_count,
        tables=metadata.table_count,
        views=metadata.view_count,
        examples=len(st.session_state.example_questions),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
