"""The sidebar: datasource, model, glossary, privacy and advanced settings."""

import os

from sqlalchemy import text
import streamlit as st

from config.model_config import CONTEXT_PROFILES, MODEL_CONFIG
from config.privacy import PrivacySettings
from database.dialects import DIALECTS, get_dialect, is_configured
from rag.embeddings import EMBEDDING_CHOICES, default_embedding_model
from services.audit import audit
from services.glossary import load_glossary, save_glossary
from ui.common import brand_html
from ui.history import render_history
from services.llm_service import DEFAULT_TIMEOUT
from services import model_catalog
from ui.indexing import NothingToIndex, _connection_from_form, add_connection, index_database, rebuild_index, refresh_example_questions, remove_connection, test_connection
from ui.state import RunSettings, all_datasources, is_indexed


SSL_MODES = ["default", "disable", "prefer", "require", "verify-full"]


CUSTOM_MODEL = "Custom..."


@st.cache_data(ttl=300, show_spinner=False)
def fetch_models(provider, api_key, base_url, nonce):

    try:

        return model_catalog.list_models(provider, api_key, base_url), None

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

        render_history(
            datasource_name,
            datasource_config,
            get_dialect(datasource_config).key,
        )

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
