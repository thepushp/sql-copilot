import os
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
from database.sql_guard import validate_sql

from rag.context_builder import build_context
from rag.embeddings import (
    EMBEDDING_CHOICES,
    default_embedding_model,
    get_embeddings,
)
from rag.retriever import (
    add_related_tables,
    retrieve_candidates,
    retrieve_documents,
)
from rag.schema_indexer import build_schema_index

from services.charting import chart_data
from services.glossary import expand_query, load_glossary, save_glossary
from services.table_selector import select_tables
from services.er_diagram import (
    COLUMN_MODES,
    MAX_TABLES,
    build_dot,
    build_mermaid,
    expand_around,
    most_connected,
    schema_tables,
    table_index,
)
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
    repair_question,
    summarize_result,
)


SSL_MODES = ["default", "disable", "prefer", "require", "verify-full"]

EXPLORER_TABLE_LIMIT = 50
CUSTOM_MODEL = "Custom..."

EXAMPLE_QUESTIONS = [
    "How many rows are in each table?",
    "Show 10 recent records from the largest table",
    "Which tables reference each other?",
]


st.set_page_config(
    page_title="RAG DB Copilot",
    page_icon="🗄️",
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

    st.session_state.conn_message = (
        "success",
        f"Added '{name}'. Click Discover & index database.",
    )


def remove_connection(name):

    st.session_state.custom_datasources.pop(name, None)

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

        return "error", f"Connection failed: {str(exc).splitlines()[0][:200]}"

    return "success", "Connection works."


def rebuild_index(embedding_model):

    filtered, policy = apply_privacy(
        st.session_state.raw_metadata,
        st.session_state.privacy,
    )

    index = build_schema_index(
        filtered.to_documents(),
        get_embeddings(embedding_model),
    )

    st.session_state.database_metadata = filtered
    st.session_state.access_policy = policy
    st.session_state.schema_index = index
    st.session_state.index_embedding_model = embedding_model


def index_database(datasource_name, embedding_model, config=None):

    with st.spinner("Discovering schemas and building index..."):

        try:

            raw = inspect_database(datasource_name, config)

            st.session_state.raw_metadata = raw
            st.session_state.indexed_datasource = datasource_name

            rebuild_index(embedding_model)

        except Exception as exc:

            st.session_state.indexed_datasource = None
            st.session_state.schema_index = None

            st.error(f"Database discovery failed: {exc}")

            return

    st.session_state.messages = []


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

        if st.button("↻", help="Refresh the model list"):

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

        st.caption(f"⚠ {dialect.read_only_warning}")

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

        with st.spinner("Rebuilding index..."):

            rebuild_index(embedding_model)

        st.toast("Privacy settings applied.")

        st.rerun()


def render_sidebar():

    with st.sidebar:

        st.header("Setup")

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
                f"⚠ {get_dialect(datasource_config).read_only_warning}"
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


def render_result(rows):

    if rows.empty:

        st.info("The query returned no rows.")

        return

    chart = chart_data(rows)

    if chart is None:

        st.dataframe(rows, width="stretch", hide_index=True)

        return

    kind, frame = chart

    table_tab, chart_tab = st.tabs(["Table", "Chart"])

    with table_tab:

        st.dataframe(rows, width="stretch", hide_index=True)

    with chart_tab:

        if kind == "line":

            st.line_chart(frame)

        else:

            st.bar_chart(frame)


def render_message(message, index):

    with st.chat_message(message["role"]):

        if message["role"] == "user":

            st.markdown(message["content"])

            return

        if message.get("error"):

            st.error(message["error"])

        elif message.get("clarify"):

            st.info(f"💬 {message['content']}")

        elif message.get("content"):

            st.markdown(message["content"])

        if message.get("summary"):

            st.markdown(f"> {message['summary']}")

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

            with st.expander(label, expanded=rows is None):

                st.code(message["sql"], language="sql")

        if rows is not None and not rows.empty:

            download, approve, _ = st.columns([1, 1, 3])

            with download:

                st.download_button(
                    "⬇ CSV",
                    rows.to_csv(index=False).encode("utf-8"),
                    file_name="result.csv",
                    mime="text/csv",
                    key=f"download_{index}",
                )

            with approve:

                st.button(
                    "✓ Saved" if message.get("approved") else "👍 Correct",
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

        if message.get("examples_used"):

            details.append(
                f"{message['examples_used']} approved example(s) used"
            )

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


def _history_text(history):

    return "\n".join(
        f"{'User' if role == 'human' else 'Assistant'}: {text[:300]}"
        for role, text in history[-4:]
    )


def answer_question(question, history, settings, status):

    message = {
        "role": "assistant",
        "question": question,
        "datasource": settings.datasource_name,
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
        )

        add_usage(response)

        message["sql"] = response.sql

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

                pass

        if result.error:

            message["error"] = f"The query failed: {result.error}"

            return message

        message["rows"] = result.rows
        message["row_count"] = result.row_count
        message["content"] = (
            f"**{result.row_count} rows** in {result.execution_ms:.0f} ms"
        )

        if settings.summarize and not result.rows.empty:

            status.update(label="Summarizing...")

            try:

                summary = summarize_result(llm, question, result.rows)

                message["summary"] = summary[0]

                add_usage(summary)

            except Exception:

                pass

        return message

    except NeedsClarification as exc:

        message["clarify"] = True
        message["content"] = str(exc)

        return message

    except NoSQLGenerated as exc:

        message["content"] = str(exc)

        return message

    except Exception as exc:

        message["error"] = str(exc)

        return message


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

        st.markdown("**Ask a question below, or try one of these:**")

        for example in EXAMPLE_QUESTIONS:

            if st.button(example, key=f"example_{example}"):

                st.session_state.pending_question = example

                st.rerun()

    for index, message in enumerate(st.session_state.messages):

        render_message(message, index)


def render_explorer():

    metadata = st.session_state.database_metadata

    if not metadata:

        st.info("Index the database to browse its schema.")

        return

    schema_name = st.selectbox(
        "Schema",
        [schema.name for schema in metadata.schemas],
        key="explorer_schema",
    )

    search = st.text_input(
        "Filter tables",
        placeholder="e.g. student",
        key="explorer_filter",
    ).strip().lower()

    schema = next(
        schema
        for schema in metadata.schemas
        if schema.name == schema_name
    )

    tables = [
        table
        for table in schema.tables
        if search in table.name.lower()
    ]

    st.caption(f"{len(tables)} tables")

    for table in tables[:EXPLORER_TABLE_LIMIT]:

        with st.expander(
            f"{table.name}"
            + (
                f"  [{table.kind}]"
                if table.kind != "table"
                else ""
            )
            + f"  ({len(table.columns)} columns)"
        ):

            if table.comment:

                st.caption(table.comment)

            st.dataframe(
                [
                    {
                        "Column": column.name,
                        "Type": column.data_type,
                        "Nullable": column.nullable,
                        "Default": column.default,
                        "PK": "✓" if column.primary_key else "",
                        "Values": ", ".join(column.values),
                        "Description": column.comment or "",
                    }
                    for column in table.columns
                ],
                width="stretch",
                hide_index=True,
            )

            for fk in table.foreign_keys:

                kind = "inferred" if fk.inferred else "FK"

                st.write(
                    f"`{fk.column}` → "
                    f"`{fk.foreign_schema}.{fk.foreign_table}."
                    f"{fk.foreign_column}` ({kind})"
                )

    if len(tables) > EXPLORER_TABLE_LIMIT:

        st.caption(
            f"Showing the first {EXPLORER_TABLE_LIMIT}. "
            "Use the filter to narrow down."
        )


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

    left, right = st.columns([2, 3])

    with left:

        mode = st.radio(
            "Show",
            ["Around a table", "Whole schema", "Tables from last answer"],
            key="er_mode",
        )

    with right:

        detail = st.radio(
            "Columns",
            COLUMN_MODES,
            horizontal=True,
            key="er_columns",
        )

        include_inferred = st.checkbox(
            "Include inferred relationships (dashed)",
            value=True,
            key="er_inferred",
            help=(
                "Relationships guessed from <name>_id columns, as opposed "
                "to real foreign keys."
            ),
        )

    keys = []

    if mode == "Around a table":

        options = sorted(index)

        start = st.selectbox(
            "Table",
            options,
            index=options.index(most_connected(index, include_inferred)),
            key="er_table",
        )

        hops = st.slider("Relationship depth", 1, 3, 1, key="er_depth")

        keys = expand_around(index, start, hops, include_inferred)

    elif mode == "Whole schema":

        schema_names = [schema.name for schema in metadata.schemas]

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
        )

        hide_isolated = st.checkbox(
            "Hide tables without relationships",
            value=True,
            key="er_hide_isolated",
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

    if not keys:

        st.info(
            "No tables to show. This schema has no relationships; "
            "untick \"Hide tables without relationships\" to list its tables."
            if mode == "Whole schema"
            else "No tables to show for this selection."
        )

        return

    if len(keys) > MAX_TABLES:

        st.warning(
            f"{len(keys)} tables match; showing the first {MAX_TABLES}. "
            "Reduce the depth or pick a smaller scope."
        )

        keys = keys[:MAX_TABLES]

    st.caption(f"{len(keys)} tables")

    dot = build_dot(index, keys, detail, include_inferred)

    st.graphviz_chart(dot, width="stretch")

    with st.expander("Export"):

        mermaid = build_mermaid(index, keys, detail, include_inferred)

        st.code(mermaid, language="text")

        first, second = st.columns(2)

        with first:

            st.download_button(
                "⬇ Mermaid (.mmd)",
                mermaid,
                file_name="er_diagram.mmd",
                key="download_mermaid",
            )

        with second:

            st.download_button(
                "⬇ Graphviz (.dot)",
                dot,
                file_name="er_diagram.dot",
                key="download_dot",
            )


def main():

    init_state()

    settings = render_sidebar()

    datasource_name = settings.datasource_name

    st.title("🗄️ RAG Database Copilot")

    tab_chat, tab_explorer, tab_er = st.tabs(
        ["💬 Chat", "🌳 Database Explorer", "🔗 ER Diagram"]
    )

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

    with tab_explorer:

        render_explorer()

    with tab_er:

        render_er_diagram()

    with tab_chat:

        render_chat(datasource_name, busy=bool(question))

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


main()
