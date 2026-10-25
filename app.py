import os
import time
from dataclasses import dataclass

from sqlalchemy import text

import streamlit as st

from config.datasource_config import DATASOURCES
from config.model_config import CONTEXT_PROFILES, MODEL_CONFIG
from config.privacy import (
    AccessPolicy,
    PrivacySettings,
    apply_privacy,
)

from database.connection_manager import get_engine
from database.dialects import DIALECTS, get_dialect, is_configured
from database.database_inspector import inspect_database
from database.query_executor import execute_query
from database.sql_guard import classify_sql, validate_sql

from rag.context_builder import build_context
from rag.embeddings import (
    EMBEDDING_CHOICES,
    default_embedding_model,
    get_embeddings,
)
from rag.retriever import (
    add_related_tables,
    documents_for_keys,
    retrieve_candidates,
    retrieve_documents,
)
from rag.schema_indexer import build_schema_index

from services.audit import audit, configure_logging, get_logger
from services.charting import chart_data
from services.exports import display_safe
from services.glossary import expand_query, load_glossary, save_glossary
from services.table_selector import select_tables
from services.er_interactive import graph_data, viewer_height, viewer_html
from ui.common import (
    APP_ICON,
    brand_html,
    NAV_CHAT,
    NAV_ER,
    NAV_EXPLORER,
    NAV_OPTIONS,
    current_theme as _theme,
    show_html,
)
from ui.explorer import open_in_er_diagram, render_explorer
from services.er_diagram import (
    COLUMN_MODES,
    DEFAULT_COLUMN_MODE,
    MAX_TABLES,
    build_dot,
    build_mermaid,
    expand_around,
    most_connected,
    schema_tables,
    table_index,
)
from services.example_questions import (
    build_candidates,
    verify_examples,
)
from services.followups import suggest_followups
from services.relationship_answer import related_tables
from services.schema_answer import answer_schema_question
from services.query_checks import (
    estimate_plan,
    find_stored_values,
    plan_label,
    plan_warnings,
    review,
)
from services.sql_explain import describe_sql
from services.sql_qualify import finalize_sql, format_sql, tables_in_sql
from services.example_store import (
    add_example,
    find_similar,
    format_examples,
)
from services.llm_service import DEFAULT_TIMEOUT, get_llm
from services.model_catalog import list_models
from services.query_service import (
    NeedsClarification,
    NoSQLGenerated,
    build_history,
    process_question,
    refine_question,
    repair_question,
    summarize_result,
)


SSL_MODES = ["default", "disable", "prefer", "require", "verify-full"]

ER_VIEWS = ["Interactive", "Static (Graphviz)"]

ER_SHOW_LABELS = {
    "Around a table": "Around table",
    "Whole schema": "Schema",
    "Tables from last answer": "Last answer",
}

# Tighter page padding, and the status text right-aligned. Only touches
# layout, never content.
BASE_CSS = """
/* The app name appears in the empty top strip only while the sidebar (which
   shows it itself) is collapsed. */
.sqlcopilot-brand { display: none !important; }
.stApp:has([data-testid="stSidebar"][aria-expanded="false"]) .sqlcopilot-brand,
.stApp:has([data-testid="stSidebar"][data-collapsed="true"]) .sqlcopilot-brand {
  display: flex !important; position: fixed; top: 0.95rem; left: 3.6rem; z-index: 1000002;
  font-weight: 600; white-space: nowrap; pointer-events: none;
}
/* Deploy targets Streamlit Community Cloud, which a self-hosted
   tool does not use. The three-dot menu (Rerun, ...) stays. */
[data-testid="stAppDeployButton"] { display: none !important; }
/* The explorer's table list reads like a file tree: left-aligned, one line each. */
.st-key-explorer_nav button { justify-content: flex-start; padding: 0.2rem 0.6rem; min-height: 2rem; }
.st-key-explorer_nav button > div, .st-key-explorer_nav button > div > span {
  justify-content: flex-start; width: 100%; min-width: 0;
}
.st-key-explorer_nav button p {
  display: block; max-width: 100%; margin: 0; text-align: left; font-size: 0.85rem;
  line-height: 1.25; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.block-container { padding-top: 4.5rem !important; padding-bottom: 1rem !important; }
"""

CUSTOM_MODEL = "Custom..."

configure_logging()

logger = get_logger("app")


st.set_page_config(
    page_title="SQL Copilot",
    page_icon=APP_ICON,
    layout="wide",
)


@dataclass
class RunSettings:
    datasource_name: str
    embedding_model: str
    provider: str
    model: str
    temperature: float
    api_key: str
    base_url: str
    timeout: int
    profile: dict
    summarize: bool
    smart_select: bool
    datasource_config: dict | None = None
    dialect_key: str = "postgresql"


def init_state():

    defaults = {
        "messages": [],
        "raw_metadata": None,
        "database_metadata": None,
        "access_policy": AccessPolicy(),
        "privacy": PrivacySettings(),
        "schema_index": None,
        "indexed_datasource": None,
        "index_embedding_model": None,
        "pending_question": None,
        "pending_example": None,
        "example_questions": [],
        "model_nonce": 0,
        "custom_datasources": {},
    }

    for key, value in defaults.items():

        if key not in st.session_state:

            st.session_state[key] = value


def is_indexed(datasource_name):

    return (
        st.session_state.schema_index is not None
        and st.session_state.indexed_datasource == datasource_name
    )


def all_datasources():

    merged = dict(DATASOURCES)

    merged.update(st.session_state.custom_datasources)

    return merged


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
        get_embeddings(embedding_model),
    )

    st.session_state.database_metadata = filtered
    st.session_state.access_policy = policy
    st.session_state.schema_index = index
    st.session_state.index_embedding_model = embedding_model


def index_database(datasource_name, embedding_model, config=None):

    started = time.perf_counter()

    with st.spinner("Discovering schemas and building index..."):

        try:

            raw = inspect_database(datasource_name, config)

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

            st.error(f"Database discovery failed: {exc}")

            return

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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_models(provider, api_key, base_url, nonce):

    try:

        return list_models(provider, api_key, base_url), None

    except Exception as exc:

        return [], str(exc)


def render_model_settings():

    provider = st.selectbox(
        "AI provider",
        list(MODEL_CONFIG.keys()),
    )

    config = MODEL_CONFIG[provider]

    api_key = ""

    if config["needs_key"]:

        api_key = st.text_input(
            "API key",
            type="password",
            key=f"api_key_{provider}",
            help=(
                f"Leave empty to use {config['key_env']} "
                "from the environment."
            ),
        )

    base_url = ""

    if config["needs_base_url"]:

        base_url = st.text_input(
            "Base URL",
            value=(
                os.getenv("OLLAMA_BASE_URL", config["default_base_url"])
                if provider == "Ollama"
                else config["default_base_url"]
            ),
            key=f"base_url_{provider}",
            placeholder="https://host/v1",
        ).strip()

    has_credentials = (
        not config["needs_key"]
        or api_key
        or (config["key_env"] and os.getenv(config["key_env"]))
    )

    live_models, error = [], None

    if has_credentials and (not config["needs_base_url"] or base_url):

        live_models, error = fetch_models(
            provider,
            api_key,
            base_url,
            st.session_state.model_nonce,
        )

    models = live_models or config["models"]

    left, right = st.columns([3, 1])

    with left:

        choice = st.selectbox(
            "Model",
            models + [CUSTOM_MODEL],
            key=f"model_{provider}",
        )

    with right:

        st.write("")

        if st.button("", icon=":material/refresh:", help="Refresh the model list"):

            st.session_state.model_nonce += 1

            st.rerun()

    if choice == CUSTOM_MODEL:

        model = st.text_input(
            "Custom model name",
            key=f"custom_model_{provider}",
        ).strip()

    else:

        model = choice

    if error:

        st.caption(f"Could not load the model list ({error[:80]}).")

    temperature = st.slider("Temperature", 0.0, 1.0, 0.1, 0.1)

    profile_names = list(CONTEXT_PROFILES.keys())

    profile_name = st.selectbox(
        "Context size",
        profile_names,
        index=profile_names.index(config["profile"]),
        key=f"profile_{provider}",
        help=(
            "How much schema is sent to the model. Compact suits small "
            "local models; Large suits big-context cloud models."
        ),
    )

    timeout = st.number_input(
        "Request timeout (s)",
        min_value=10,
        max_value=900,
        value=DEFAULT_TIMEOUT,
        step=10,
    )

    smart_select = st.checkbox(
        "Smart table selection",
        value=True,
        help=(
            "Asks the model to pick the needed tables from a shortlist "
            "before writing SQL. One extra model call; much better at "
            "choosing the right tables."
        ),
    )

    summarize = st.checkbox(
        "Summarize results with the model",
        value=False,
        help=(
            "Sends the first 20 result rows to the model. "
            "Leave off if results contain sensitive data."
        ),
    )

    return (
        provider,
        model,
        temperature,
        api_key,
        base_url,
        int(timeout),
        CONTEXT_PROFILES[profile_name],
        summarize,
        smart_select,
    )


def render_connection_form():

    st.caption(
        "Credentials entered here are kept only for this browser session "
        "and are never written to disk. For permanent connections, use "
        "the .env file."
    )

    dialect_key = st.selectbox(
        "Database type",
        list(DIALECTS),
        format_func=lambda key: DIALECTS[key].label,
        key="conn_type",
    )

    dialect = DIALECTS[dialect_key]

    st.text_input("Connection name", key="conn_name", placeholder="Prod replica")

    if dialect.needs_host:

        host, port = st.columns([3, 2])

        with host:

            st.text_input("Host", key="conn_host", value="localhost")

        with port:

            st.number_input(
                "Port",
                key=f"conn_port_{dialect_key}",
                min_value=1,
                max_value=65535,
                value=dialect.default_port,
            )

    st.text_input(dialect.database_label, key="conn_db")

    if dialect.needs_host:

        st.text_input("Username", key="conn_user")

        st.text_input("Password", key="conn_password", type="password")

    if dialect.supports_ssl_mode:

        st.selectbox(
            "SSL mode",
            SSL_MODES,
            key="conn_ssl",
            help="Use 'require' for most cloud databases.",
        )

    if dialect.read_only_warning:

        st.caption(f":material/warning: {dialect.read_only_warning}")

    test, add = st.columns(2)

    with test:

        if st.button("Test", width="stretch", key="conn_test"):

            st.session_state.conn_message = test_connection(
                _connection_from_form(),
                "connection-test",
            )

    with add:

        st.button(
            "Add",
            width="stretch",
            key="conn_add",
            type="primary",
            on_click=add_connection,
        )

    message = st.session_state.pop("conn_message", None)

    if message:

        getattr(st, message[0])(message[1])


def render_glossary():

    st.caption(
        "Define your own terms, one per line, as `term: meaning`. "
        "They are used to find tables and are given to the model."
    )

    text = st.text_area(
        "Glossary",
        value=load_glossary(),
        height=160,
        label_visibility="collapsed",
        placeholder=(
            "bus: the vehicle table\n"
            "route: a run (public.run)\n"
            "active students: student where status = 'ACTIVE'"
        ),
    )

    if st.button("Save glossary", width="stretch"):

        save_glossary(text)

        st.toast("Glossary saved.")


def render_privacy(embedding_model):

    raw = st.session_state.raw_metadata

    if raw is None:

        st.caption("Available after the database is discovered.")

        return

    current = st.session_state.privacy

    schemas = [schema.name for schema in raw.schemas]

    tables = [
        f"{table.schema}.{table.name}"
        for schema in raw.schemas
        for table in schema.tables
    ]

    excluded_schemas = st.multiselect(
        "Exclude schemas",
        schemas,
        default=[s for s in current.excluded_schemas if s in schemas],
    )

    excluded_tables = st.multiselect(
        "Exclude tables",
        tables,
        default=[t for t in current.excluded_tables if t in tables],
    )

    patterns = st.text_input(
        "Hide columns matching",
        value=", ".join(current.column_patterns),
        help="Comma-separated patterns, e.g. *password*, ssn, *_email",
    )

    include_values = st.checkbox(
        "Include sample values in the index",
        value=current.include_sample_values,
        help="Common values of low-cardinality text columns.",
    )

    hide_infrastructure = st.checkbox(
        "Hide infrastructure tables",
        value=current.hide_infrastructure,
        help=(
            "Migration history, job schedulers and statistics tables "
            "(flyway, quartz, shedlock, pg_stat_*). Keeps the model "
            "focused on business data."
        ),
    )

    if st.button("Apply privacy settings", width="stretch"):

        previous = st.session_state.privacy

        st.session_state.privacy = PrivacySettings(
            excluded_schemas=excluded_schemas,
            excluded_tables=excluded_tables,
            column_patterns=[
                pattern.strip()
                for pattern in patterns.split(",")
                if pattern.strip()
            ],
            include_sample_values=include_values,
            hide_infrastructure=hide_infrastructure,
        )

        try:

            with st.spinner("Rebuilding index..."):

                rebuild_index(embedding_model)

        except NothingToIndex as exc:

            st.session_state.privacy = previous

            st.error(f"Not applied: {exc}")

            return

        refresh_example_questions()

        audit(
            "privacy",
            excluded_schemas=len(excluded_schemas),
            excluded_tables=len(excluded_tables),
            hidden_column_patterns=len(st.session_state.privacy.column_patterns),
            include_sample_values=include_values,
            hide_infrastructure=hide_infrastructure,
        )

        st.toast("Privacy settings applied.")

        st.rerun()


def render_sidebar():

    with st.sidebar:

        st.html(brand_html(28))

        st.caption("Ask your database in plain English")

        st.divider()

        datasources = all_datasources()

        datasource_name = st.selectbox(
            "Datasource",
            list(datasources),
            key="datasource_select",
            **(
                {}
                if "datasource_select" in st.session_state
                else {
                    "index": next(
                        (
                            position
                            for position, name in enumerate(datasources)
                            if is_configured(datasources[name])
                        ),
                        0,
                    )
                }
            ),
            format_func=lambda name: (
                f"{name} · {get_dialect(datasources[name]).label}"
                if is_configured(datasources[name])
                else f"{name} (not configured)"
            ),
        )

        datasource_config = datasources[datasource_name]

        if get_dialect(datasource_config).read_only_warning:

            st.caption(
                f":material/warning: {get_dialect(datasource_config).read_only_warning}"
            )

        if datasource_name in st.session_state.custom_datasources:

            st.button(
                "Remove this connection",
                on_click=remove_connection,
                args=(datasource_name,),
                width="stretch",
            )

        with st.expander("Add a connection", expanded=False):

            render_connection_form()

        embedding_options = list(EMBEDDING_CHOICES)

        if default_embedding_model() not in embedding_options:

            embedding_options.insert(0, default_embedding_model())

        embedding_model = st.session_state.get(
            "embedding_model",
            default_embedding_model(),
        )

        if st.button(
            "Discover & index database",
            width="stretch",
            type=(
                "secondary"
                if is_indexed(datasource_name)
                else "primary"
            ),
        ):

            index_database(
                datasource_name,
                embedding_model,
                datasource_config,
            )

        metadata = st.session_state.database_metadata

        if is_indexed(datasource_name) and metadata:

            st.success(
                f"Ready: {metadata.schema_count} schemas, "
                f"{metadata.table_count} tables, "
                f"{metadata.view_count} views"
            )

        else:

            st.warning("Not indexed yet")

        st.divider()

        with st.expander("Model & provider", expanded=False):

            model_settings = render_model_settings()

        with st.expander("Business glossary", expanded=False):

            render_glossary()

        with st.expander("Privacy", expanded=False):

            render_privacy(embedding_model)

        with st.expander("Advanced", expanded=False):

            st.selectbox(
                "Embedding model",
                embedding_options,
                index=embedding_options.index(
                    default_embedding_model()
                ),
                key="embedding_model",
                help="Change, then re-index. Larger models match better "
                "but download once and run slower.",
            )

        st.divider()

        if st.button(
            "Clear chat",
            width="stretch",
            disabled=not st.session_state.messages,
        ):

            st.session_state.messages = []

            st.rerun()

    (
        provider,
        model,
        temperature,
        api_key,
        base_url,
        timeout,
        profile,
        summarize,
        smart_select,
    ) = model_settings

    return RunSettings(
        datasource_name=datasource_name,
        embedding_model=embedding_model,
        provider=provider,
        model=model,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        profile=profile,
        summarize=summarize,
        smart_select=smart_select,
        datasource_config=datasource_config,
        dialect_key=get_dialect(datasource_config).key,
    )


def approve_message(index):

    message = st.session_state.messages[index]

    add_example(
        message["datasource"],
        message["question"],
        message["sql"],
    )

    message["approved"] = True

    audit(
        "approve",
        datasource=message["datasource"],
        question=message["question"],
        sql=message["sql"],
    )


def annotate(message, config):
    """Fill in the plain-English plan, plan estimate and plausibility warnings."""

    dialect_key = message.get("dialect", "postgresql")

    message["plan"] = describe_sql(message["sql"], dialect_key)

    rows = message.get("rows")

    issues = []
    estimate = None

    try:

        issues = review(
            message["sql"],
            rows,
            dialect_key,
            st.session_state.database_metadata,
        )

        if rows is not None and not message.get("example"):

            estimate = estimate_plan(
                message["datasource"],
                message["sql"],
                config,
                dialect_key,
            )

    except Exception:

        logger.warning("Query checks failed", exc_info=True)

    message["issues"] = issues
    message["warnings"] = [issue.text for issue in issues] + plan_warnings(estimate)
    message["estimate"] = plan_label(estimate)


def run_edited_sql(index):
    """Run SQL the user edited in an answer. Same guard and executor as every query."""

    message = st.session_state.messages[index]

    sql = st.session_state.get(f"edit_sql_{index}", "").strip().rstrip(";").strip()

    dialect_key = message.get("dialect", "postgresql")
    config = all_datasources().get(message["datasource"])

    message.pop("edit_error", None)

    try:

        operation = classify_sql(sql, dialect_key)

        if operation != "READ":

            raise ValueError(
                f"Only SELECT queries can be run here (this is {operation})."
            )

        validation = validate_sql(sql, st.session_state.access_policy, dialect_key)

        if not validation.allowed:
            raise ValueError(f"Rejected by the safety guard: {validation.reason}")

    except Exception as exc:

        message["edit_error"] = str(exc)

        return

    result = execute_query(message["datasource"], sql, config)

    audit(
        "edit",
        datasource=message["datasource"],
        dialect=dialect_key,
        question=message.get("question"),
        sql=sql,
        outcome="error" if result.error else "ok",
        rows=result.row_count,
        elapsed_ms=round(result.execution_ms),
        error=result.error,
    )

    if result.error:

        message["edit_error"] = result.error

        return

    message["sql"] = sql
    message["rows"] = result.rows
    message["row_count"] = result.row_count
    message["content"] = f"**{result.row_count} rows** in {result.execution_ms:.0f} ms"
    message["edited"] = True

    for stale in ("error", "blocked", "summary", "corrected", "approved", "example"):
        message.pop(stale, None)

    annotate(message, config)


def ask_followup(text):

    st.session_state.pending_question = text


def render_result(rows):

    if rows.empty:

        st.info("The query returned no rows.")

        return

    chart = chart_data(rows)

    if chart is None:

        st.dataframe(display_safe(rows), width="stretch", hide_index=True)

        return

    kind, frame = chart

    table_tab, chart_tab = st.tabs(["Table", "Chart"])

    with table_tab:

        st.dataframe(display_safe(rows), width="stretch", hide_index=True)

    with chart_tab:

        if kind == "line":

            st.line_chart(frame)

        else:

            st.bar_chart(frame)


def open_er_from_chat(key):

    if key:

        open_in_er_diagram(key)

    else:

        st.session_state["er_mode"] = "Whole schema"
        st.session_state["nav"] = NAV_ER


def render_message(message, index, followups=False):

    with st.chat_message(message["role"]):

        if message["role"] == "user":

            st.markdown(message["content"])

            return

        if message.get("error"):

            st.error(message["error"])

        elif message.get("clarify"):

            st.info(message["content"], icon=":material/help:")

        elif message.get("content"):

            st.markdown(message["content"])

        if message.get("er_tables") is not None:

            with st.container(horizontal=True):

                for key in message["er_tables"] or [None]:

                    st.button(
                        f"Open {key} in the ER diagram" if key else "Open the ER diagram",
                        icon=":material/schema:",
                        key=f"er_{index}_{key}",
                        on_click=open_er_from_chat,
                        args=(key,),
                    )

        if message.get("summary"):

            st.markdown(f"> {message['summary']}")

        if message.get("plan"):

            st.caption(message["plan"])

        if message.get("warnings"):

            st.warning(
                "\n".join(f"- {text}" for text in message["warnings"]),
                icon=":material/warning:",
            )

        if message.get("blocked"):

            st.warning(
                "Review only: this SQL was not executed. "
                "DML and DDL are never run by the app."
            )

        rows = message.get("rows")

        if rows is not None:

            render_result(rows)

        if message.get("sql"):

            label = (
                "SQL (auto-corrected after an error)"
                if message.get("corrected")
                else "SQL"
            )

            if message.get("edited"):
                label += " (edited)"

            with st.expander(label, expanded=rows is None):

                if message.get("blocked"):

                    st.code(message["sql"], language="sql")

                else:

                    st.text_area(
                        "SQL",
                        value=message["sql"],
                        key=f"edit_sql_{index}",
                        height="content",
                        label_visibility="collapsed",
                    )

                    st.button(
                        "Run edited SQL",
                        key=f"run_edit_{index}",
                        icon=":material/play_arrow:",
                        on_click=run_edited_sql,
                        args=(index,),
                        help="Runs this SQL through the same safety guard.",
                    )

                    if message.get("edit_error"):

                        st.error(message["edit_error"])

        if rows is not None and not rows.empty:

            with st.container(horizontal=True):

                st.download_button(
                    "CSV",
                    rows.to_csv(index=False).encode("utf-8"),
                    file_name="result.csv",
                    mime="text/csv",
                    key=f"download_{index}",
                    icon=":material/download:",
                )

                st.button(
                    "Saved" if message.get("approved") else "Correct",
                    icon=":material/check:" if message.get("approved") else ":material/thumb_up:",
                    key=f"approve_{index}",
                    on_click=approve_message,
                    args=(index,),
                    disabled=bool(message.get("approved")),
                    help=(
                        "Save this question and SQL as a verified example "
                        "for future similar questions."
                    ),
                )

        details = []

        if message.get("example"):

            details.append("Verified example, answered without the model")

        if message.get("examples_used"):

            details.append(
                f"{message['examples_used']} approved example(s) used"
            )

        if message.get("elapsed_ms"):

            details.append(f"{message['elapsed_ms'] / 1000:.1f} s")

        if message.get("estimate"):

            details.append(message["estimate"])

        if message.get("input_tokens") or message.get("output_tokens"):

            details.append(
                f"{message['input_tokens']:,} tokens in / "
                f"{message['output_tokens']:,} out"
            )

        if message.get("tables"):

            details.append(
                "Tables considered: " + ", ".join(message["tables"])
            )

        if details:

            st.caption(" · ".join(details))

        if followups and rows is not None:

            suggestions = suggest_followups(rows, message.get("sql", ""))

            if suggestions:

                with st.container(horizontal=True):

                    for position, text in enumerate(suggestions):

                        st.button(
                            text,
                            key=f"followup_{index}_{position}",
                            on_click=ask_followup,
                            args=(text,),
                            type="tertiary",
                            icon=":material/subdirectory_arrow_right:",
                        )


def _history_text(history):

    return "\n".join(
        f"{'User' if role == 'human' else 'Assistant'}: {text[:300]}"
        for role, text in history[-4:]
    )


def _answer_question(question, history, settings, status):

    message = {
        "role": "assistant",
        "question": question,
        "datasource": settings.datasource_name,
        "dialect": settings.dialect_key,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    def add_usage(response_or_tuple):

        if isinstance(response_or_tuple, tuple):

            _, tokens_in, tokens_out = response_or_tuple

        else:

            tokens_in = response_or_tuple.input_tokens
            tokens_out = response_or_tuple.output_tokens

        message["input_tokens"] += tokens_in
        message["output_tokens"] += tokens_out

    profile = settings.profile
    policy = st.session_state.access_policy

    try:

        relationships = answer_schema_question(
            question,
            st.session_state.database_metadata,
        )

        if relationships:

            message["content"] = relationships

            tables = related_tables(
                question,
                st.session_state.database_metadata,
            )

            if tables is not None:
                message["er_tables"] = tables[:3]

            return message

        glossary = load_glossary()

        search_query = expand_query(question, glossary)

        llm = get_llm(
            provider=settings.provider,
            model=settings.model,
            temperature=settings.temperature,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )

        index = st.session_state.schema_index

        documents = None

        if settings.smart_select:

            status.update(label="Choosing relevant tables...")

            candidates = retrieve_candidates(
                index,
                search_query,
                limit=profile["shortlist"],
                fetch_k=profile["fetch_k"],
            )

            try:

                chosen, tokens_in, tokens_out = select_tables(
                    llm,
                    question,
                    candidates,
                    history_text=_history_text(history),
                    glossary=glossary,
                    max_tables=profile["k"],
                )

                message["input_tokens"] += tokens_in
                message["output_tokens"] += tokens_out

                if chosen:

                    documents = add_related_tables(
                        index,
                        chosen,
                        profile["max_neighbors"],
                    )

            except Exception:

                logger.warning(
                    "Table selection failed; using ranked tables",
                    exc_info=True,
                )

                documents = None

            if documents is None:

                documents = add_related_tables(
                    index,
                    candidates[: profile["k"]],
                    profile["max_neighbors"],
                )

        else:

            status.update(label="Finding relevant tables...")

            documents = retrieve_documents(index, search_query, profile)

        metadata = st.session_state.database_metadata

        previous_sql = next(
            (
                item["sql"]
                for item in reversed(st.session_state.messages)
                if item["role"] == "assistant" and item.get("sql")
            ),
            "",
        )

        if history and previous_sql:

            present = {
                document.metadata.get("key")
                for document in documents
            }

            documents = [
                document
                for document in documents_for_keys(
                    index,
                    tables_in_sql(previous_sql, metadata, settings.dialect_key),
                )
                if document.metadata.get("key") not in present
            ] + documents

        context = build_context(documents, profile)

        message["tables"] = [
            document.metadata["key"]
            for document in documents
            if "key" in document.metadata
        ]

        try:

            found = find_similar(
                get_embeddings(st.session_state.index_embedding_model),
                st.session_state.index_embedding_model,
                settings.datasource_name,
                question,
                profile["examples"],
            )

        except Exception:

            logger.warning("Approved-example lookup failed", exc_info=True)

            found = []

        examples = format_examples(found)

        message["examples_used"] = len(found)

        status.update(label="Generating SQL...")

        response = process_question(
            llm=llm,
            question=question,
            context=context,
            history=history,
            examples=examples,
            glossary=glossary,
            dialect_key=settings.dialect_key,
            previous_sql=previous_sql if history else "",
        )

        add_usage(response)

        response.sql = finalize_sql(response.sql, metadata, settings.dialect_key)

        message["sql"] = response.sql

        message["plan"] = describe_sql(response.sql, settings.dialect_key)

        if response.operation != "READ":

            message["content"] = (
                f"Generated **{response.operation}** SQL for review."
            )

            message["blocked"] = True

            return message

        validation = validate_sql(
            response.sql,
            policy,
            settings.dialect_key,
        )

        if not validation.allowed:

            message["error"] = (
                "The generated SQL was rejected by the "
                f"safety guard: {validation.reason}"
            )

            return message

        status.update(label="Running query...")

        result = execute_query(
            settings.datasource_name,
            response.sql,
            settings.datasource_config,
        )

        if result.error:

            status.update(label="Query failed, retrying...")

            try:

                repaired = repair_question(
                    llm=llm,
                    question=question,
                    context=context,
                    history=history,
                    failed_sql=response.sql,
                    error=result.error,
                    examples=examples,
                    glossary=glossary,
                    dialect_key=settings.dialect_key,
                )

                add_usage(repaired)

                repaired.sql = finalize_sql(repaired.sql, metadata, settings.dialect_key)

                if validate_sql(
                    repaired.sql,
                    policy,
                    settings.dialect_key,
                ).allowed:

                    retry = execute_query(
                        settings.datasource_name,
                        repaired.sql,
                        settings.datasource_config,
                    )

                    if not retry.error:

                        result = retry
                        message["sql"] = repaired.sql
                        message["corrected"] = True

            except Exception:

                logger.warning("Automatic repair failed", exc_info=True)

        if result.error:

            message["error"] = f"The query failed: {result.error}"

            return message

        if result.rows.empty:

            fixable = [
                issue
                for issue in review(
                    message["sql"],
                    result.rows,
                    settings.dialect_key,
                    st.session_state.database_metadata,
                )
                if issue.fixable
            ]

            problems = [issue.text for issue in fixable]

            if fixable:

                try:

                    problems += find_stored_values(
                        fixable,
                        settings.datasource_name,
                        settings.datasource_config,
                        settings.dialect_key,
                        lambda sql: validate_sql(
                            sql,
                            policy,
                            settings.dialect_key,
                        ).allowed,
                    )

                except Exception:

                    logger.warning("Stored-value lookup failed", exc_info=True)

            if problems:

                status.update(label="No rows, checking filter values...")

                try:

                    refined = refine_question(
                        llm=llm,
                        question=question,
                        context=context,
                        history=history,
                        sql=message["sql"],
                        problems=problems,
                        examples=examples,
                        glossary=glossary,
                        dialect_key=settings.dialect_key,
                    )

                    add_usage(refined)

                    refined.sql = finalize_sql(refined.sql, metadata, settings.dialect_key)

                    if validate_sql(
                        refined.sql,
                        policy,
                        settings.dialect_key,
                    ).allowed:

                        retry = execute_query(
                            settings.datasource_name,
                            refined.sql,
                            settings.datasource_config,
                        )

                        if not retry.error and not retry.rows.empty:

                            result = retry
                            message["sql"] = refined.sql
                            message["corrected"] = True

                except Exception:

                    logger.warning("Empty-result retry failed", exc_info=True)

        message["rows"] = result.rows
        message["row_count"] = result.row_count
        message["content"] = (
            f"**{result.row_count} rows** in {result.execution_ms:.0f} ms"
        )

        annotate(message, settings.datasource_config)

        if settings.summarize and not result.rows.empty:

            status.update(label="Summarizing...")

            try:

                summary = summarize_result(llm, question, result.rows)

                message["summary"] = summary[0]

                add_usage(summary)

            except Exception:

                logger.warning("Result summary failed", exc_info=True)

        return message

    except NeedsClarification as exc:

        message["clarify"] = True
        message["content"] = str(exc)

        return message

    except NoSQLGenerated as exc:

        message["content"] = str(exc)

        return message

    except Exception as exc:

        logger.warning("Answer failed: %s", exc, exc_info=True)

        message["error"] = str(exc)

        return message


def _outcome(reply):

    if reply.get("error"):

        return "rejected" if "safety guard" in reply["error"] else "error"

    if reply.get("clarify"):
        return "clarify"

    if reply.get("blocked"):
        return "blocked"

    if reply.get("rows") is not None:
        return "ok"

    return "no_sql"


def answer_question(question, history, settings, status):
    """Answer a question and record it in the audit trail."""

    started = time.perf_counter()

    reply = _answer_question(question, history, settings, status)

    reply["elapsed_ms"] = round((time.perf_counter() - started) * 1000)

    audit(
        "answer",
        datasource=settings.datasource_name,
        dialect=settings.dialect_key,
        provider=settings.provider,
        model=settings.model,
        question=question,
        sql=reply.get("sql"),
        outcome=_outcome(reply),
        rows=reply.get("row_count"),
        corrected=reply.get("corrected"),
        warnings=reply.get("warnings") or None,
        tables=reply.get("tables"),
        examples_used=reply.get("examples_used"),
        input_tokens=reply.get("input_tokens"),
        output_tokens=reply.get("output_tokens"),
        elapsed_ms=reply["elapsed_ms"],
        error=reply.get("error"),
    )

    return reply


def render_chat(datasource_name, busy):

    indexed = is_indexed(datasource_name)

    if busy:

        pass

    elif not st.session_state.messages and not indexed:

        st.info(
            "**Get started**\n\n"
            "1. Pick a datasource in the sidebar.\n"
            "2. Click **Discover & index database**.\n"
            "3. Ask a question in the box at the bottom."
        )

    elif not st.session_state.messages:

        examples = st.session_state.example_questions

        if examples:

            with st.container(border=True):

                st.markdown(
                    "**Try an example.** Runs instantly on your data, "
                    "no model needed. Or ask your own question below."
                )

                columns = st.columns(2)

                for position, example in enumerate(examples):

                    with columns[position % 2]:

                        if st.button(
                            example.question,
                            key=f"example_{position}",
                            width="stretch",
                        ):

                            st.session_state.pending_example = position

                            st.rerun()

        else:

            st.markdown("**Ask a question about your database below.**")

    last = len(st.session_state.messages) - 1

    for index, message in enumerate(st.session_state.messages):

        render_message(message, index, followups=index == last and not busy)


def _last_answer_tables():

    for message in reversed(st.session_state.messages):

        if message["role"] == "assistant" and message.get("tables"):

            return message["tables"]

    return []


def render_er_diagram():

    metadata = st.session_state.database_metadata

    if not metadata:

        st.info("Index the database to draw its relationships.")

        return

    index = table_index(metadata)

    # Controls keep their natural width and wrap onto a second row when the
    # window is narrow, instead of being squeezed.
    with st.container(horizontal=True, vertical_alignment="bottom", wrap=True):

        show_slot = st.container(width="content")
        pick_slot = st.container(width="content")
        extra_slot = st.container(width="content")
        columns_slot = st.container(width="content")
        view_slot = st.container(width="content")

    # The buttons show the current choice, so nothing has to be opened to see it.
    hide_isolated = False

    columns_now = st.session_state.get("er_columns", DEFAULT_COLUMN_MODE)
    view_now = st.session_state.get("er_view", ER_VIEWS[0])

    with columns_slot:

        with st.popover(f"Columns: {columns_now}"):

            detail = st.radio(
                "What to show inside each table",
                COLUMN_MODES,
                captions=[
                    "Primary and foreign keys only",
                    "Every column (very long tables are trimmed)",
                    "Just the table names",
                ],
                index=COLUMN_MODES.index(DEFAULT_COLUMN_MODE),
                key="er_columns",
            )

    with view_slot:

        with st.popover(f"View: {view_now.split(' ')[0]}"):

            er_view = st.radio(
                "How to draw the diagram",
                ER_VIEWS,
                captions=[
                    "Click a column, line or table to highlight its links",
                    "A fixed picture (Graphviz)",
                ],
                key="er_view",
            )

            include_inferred = st.toggle(
                "Show inferred relationships (dashed)",
                value=True,
                key="er_inferred",
                help=(
                    "Relationships guessed from <name>_id columns, as "
                    "opposed to real foreign keys."
                ),
            )

            if st.session_state.get("er_mode") == "Whole schema":

                hide_isolated = st.toggle(
                    "Hide tables without relationships",
                    value=False,
                    key="er_hide_isolated",
                )

    with show_slot:

        mode = st.segmented_control(
            "Show",
            list(ER_SHOW_LABELS),
            default="Around a table",
            required=True,
            key="er_mode",
            format_func=ER_SHOW_LABELS.get,
        )

    keys = []

    if mode == "Around a table":

        options = sorted(index)

        with pick_slot:

            start = st.selectbox(
                "Table",
                options,
                index=options.index(most_connected(index, include_inferred)),
                key="er_table",
                width=190,
            )

        with extra_slot:

            hops = st.slider("Depth", 1, 3, 1, key="er_depth", width=130)

        keys = expand_around(index, start, hops, include_inferred)

    elif mode == "Whole schema":

        schema_names = [schema.name for schema in metadata.schemas]

        with pick_slot:

            schema = st.selectbox(
                "Schema",
                schema_names,
                index=schema_names.index(
                    max(
                        schema_names,
                        key=lambda name: len(
                            schema_tables(index, name, include_inferred)
                        ),
                    )
                ),
                key="er_schema",
                width=190,
            )

        keys = schema_tables(
            index,
            schema,
            include_inferred,
            hide_isolated,
        )

    else:

        keys = [key for key in _last_answer_tables() if key in index]

        if not keys:

            st.info("Ask a question first; its tables will appear here.")

            return

        with pick_slot:

            st.caption("Tables used by your last question")

    if not keys:

        st.info(
            "No tables to show. This schema has no relationships; "
            "turn off \"Hide tables without relationships\" to list its tables."
            if mode == "Whole schema"
            else "No tables to show for this selection."
        )

        return

    total = len(keys)

    if total > MAX_TABLES:

        keys = keys[:MAX_TABLES]

    shown = len(keys)

    data = graph_data(
        index,
        keys,
        detail,
        include_inferred,
        focus=keys[0],
        theme=_theme(),
    )

    summary = f"{shown} tables · {len(data['edges'])} relationships"

    if total > shown:

        summary = (
            f"showing {shown} of {total} tables "
            f"· {len(data['edges'])} relationships"
        )

    data["summary"] = summary

    dot = build_dot(index, keys, detail, include_inferred)

    mermaid = build_mermaid(index, keys, detail, include_inferred)

    data["exports"] = {"mermaid": mermaid, "dot": dot}

    # Small diagrams get a compact frame; larger ones use the rest of the window.
    frame_height = (
        "max(420px, calc(100vh - 250px))"
        if len(keys) > 4
        else f"{viewer_height(len(keys))}px"
    )

    with st.container(key="er_canvas"):

        st.html(
            f"<style>.st-key-er_canvas iframe {{ height: {frame_height} "
            "!important; }</style>"
        )

        if er_view == "Interactive":

            show_html(viewer_html(data), height=viewer_height(len(keys)))

        else:

            with st.container(horizontal=True, vertical_alignment="center"):

                st.caption(summary)

                st.download_button(
                    "Mermaid",
                    mermaid,
                    file_name="er_diagram.mmd",
                    key="download_mermaid",
                    icon=":material/download:",
                )

                st.download_button(
                    "Graphviz",
                    dot,
                    file_name="er_diagram.dot",
                    key="download_dot",
                    icon=":material/download:",
                )

            st.graphviz_chart(dot, width="stretch")


def render_header(settings):
    """Slim top bar: section switcher on the left, connection status on the right."""

    metadata = st.session_state.database_metadata

    with st.container(
        horizontal=True,
        vertical_alignment="center",
        horizontal_alignment="distribute",
        wrap=True,
    ):

        view = st.segmented_control(
            "Section",
            NAV_OPTIONS,
            default=NAV_CHAT,
            required=True,
            key="nav",
            label_visibility="collapsed",
        )

        if is_indexed(settings.datasource_name) and metadata:

            st.caption(
                f"{settings.datasource_name} · "
                f"{get_dialect(settings.datasource_config).label} · "
                f"{metadata.table_count} tables, "
                f"{metadata.view_count} views"
            )

        else:

            st.caption("Not indexed yet. Use the sidebar to get started.")

    return view or NAV_CHAT


def render_chat_view(settings):

    datasource_name = settings.datasource_name

    indexed = is_indexed(datasource_name)

    typed = st.chat_input(
        (
            "Ask a question about your database..."
            if indexed
            else "Index the database first (sidebar)"
        ),
        disabled=not indexed,
    )

    question = typed or st.session_state.pending_question

    st.session_state.pending_question = None

    example_position = st.session_state.pending_example

    st.session_state.pending_example = None

    render_chat(
        datasource_name,
        busy=bool(question) or example_position is not None,
    )

    if example_position is not None and indexed:

        examples = st.session_state.example_questions

        if example_position < len(examples):

            example = examples[example_position]

            user_message = {"role": "user", "content": example.question}

            st.session_state.messages.append(user_message)

            render_message(
                user_message,
                len(st.session_state.messages) - 1,
            )

            with st.spinner("Running example..."):

                reply = run_example(example, settings)

            st.session_state.messages.append(reply)

            st.rerun()

    if question and indexed:

        if not settings.model:

            st.warning("Choose or enter a model in the sidebar.")

            return

        history = build_history(
            st.session_state.messages,
            settings.profile["history_turns"],
        )

        user_message = {"role": "user", "content": question}

        st.session_state.messages.append(user_message)

        render_message(
            user_message,
            len(st.session_state.messages) - 1,
        )

        with st.chat_message("assistant"):

            with st.status("Working...", expanded=False) as status:

                reply = answer_question(
                    question,
                    history,
                    settings,
                    status,
                )

                failed = bool(reply.get("error"))

                status.update(
                    label="Failed" if failed else "Done",
                    state="error" if failed else "complete",
                )

        st.session_state.messages.append(reply)

        st.rerun()


def main():

    init_state()

    settings = render_sidebar()

    st.html(f"<style>{BASE_CSS}</style>")

    st.html(brand_html(20, "sqlcopilot-brand"))

    view = render_header(settings)

    # Only the active section is built, so switching away from the chat does
    # not redraw the diagram on every message.
    if view == NAV_EXPLORER:

        render_explorer(settings)

    elif view == NAV_ER:

        render_er_diagram()

    else:

        render_chat_view(settings)


main()
