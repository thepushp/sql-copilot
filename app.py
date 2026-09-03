import streamlit as st

from config.model_config import MODEL_CONFIG
from config.datasource_config import DATASOURCES
from database.schema_inspector import get_schema_documents
from database.sql_guard import validate_sql
from database.query_executor import execute_query
from rag.embeddings import get_embeddings
from rag.schema_indexer import build_schema_index
from rag.retriever import create_retriever
from services.llm_service import get_llm
from services.sql_generation_service import generate_sql


st.set_page_config(
    page_title="RAG DB Assistant",
    page_icon="🗄️",
    layout="wide",
)


# --------------------------------------------------
# Session State
# --------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "schema_index" not in st.session_state:
    st.session_state.schema_index = None

if "schema_docs" not in st.session_state:
    st.session_state.schema_docs = []

if "indexed_datasource" not in st.session_state:
    st.session_state.indexed_datasource = None


def clear_chat():
    st.session_state.messages = []


# --------------------------------------------------
# Sidebar
# --------------------------------------------------

with st.sidebar:

    st.header("⚙️ Configuration")

    datasource_name = st.selectbox(
        "Datasource",
        list(DATASOURCES.keys()),
    )

    provider = st.selectbox(
        "AI Provider",
        list(MODEL_CONFIG.keys()),
    )

    model = st.selectbox(
        "Model",
        MODEL_CONFIG[provider]["models"],
    )

    temperature = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=0.1,
        step=0.1,
    )

    st.divider()

    if st.button(
        "🔄 Index Database Schema",
        use_container_width=True,
    ):

        with st.spinner(
            "Reading database schema..."
        ):

            try:

                docs = get_schema_documents(
                    datasource_name
                )

                embeddings = get_embeddings()

                st.session_state.schema_index = (
                    build_schema_index(
                        docs,
                        embeddings,
                    )
                )

                st.session_state.schema_docs = docs

                st.session_state.indexed_datasource = (
                    datasource_name
                )

                st.success(
                    f"Indexed {len(docs)} "
                    "schema documents."
                )

            except Exception as exc:

                st.error(
                    f"Schema indexing failed: {exc}"
                )

    if st.button(
        "🗑️ Clear Chat",
        use_container_width=True,
    ):

        clear_chat()
        st.rerun()

    st.divider()

    st.caption(
        f"Datasource: {datasource_name}"
    )

    st.caption(
        f"Provider: {provider}"
    )

    st.caption(
        f"Model: {model}"
    )


# --------------------------------------------------
# Main UI
# --------------------------------------------------

st.title("🗄️ RAG Database Assistant")

st.caption(
    "Ask questions about your database. "
    "The assistant generates and executes "
    "read-only SQL."
)


# --------------------------------------------------
# Schema status
# --------------------------------------------------

if (
    st.session_state.indexed_datasource
    != datasource_name
):

    st.info(
        "Click **Index Database Schema** "
        "before asking database questions."
    )


# --------------------------------------------------
# Chat history
# --------------------------------------------------

for message in st.session_state.messages:

    with st.chat_message(
        message["role"]
    ):

        st.markdown(
            message["content"]
        )

        if message.get("sql"):

            st.code(
                message["sql"],
                language="sql",
            )

        if message.get("error"):

            st.error(
                message["error"]
            )

        if message.get("rows") is not None:

            st.dataframe(
                message["rows"],
                use_container_width=True,
            )

            st.caption(
                f'{message["row_count"]} rows • '
                f'{message["execution_ms"]:.2f} ms'
            )


# --------------------------------------------------
# User input
# --------------------------------------------------

question = st.chat_input(
    "e.g. Give me students added after 2026-01-01"
)


if question:

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    with st.chat_message("user"):

        st.markdown(question)

    with st.chat_message("assistant"):

        if (
            st.session_state.schema_index
            is None
        ):

            st.warning(
                "Please index the database "
                "schema first."
            )

            st.stop()

        try:

            # ------------------------------------------
            # Retrieve schema
            # ------------------------------------------

            with st.spinner(
                "Understanding your question..."
            ):

                retriever = create_retriever(
                    st.session_state.schema_index
                )

                schema_docs = (
                    retriever.invoke(question)
                )

                llm = get_llm(
                    provider=provider,
                    model=model,
                    temperature=temperature,
                )

                sql = generate_sql(
                    llm=llm,
                    question=question,
                    schema_docs=schema_docs,
                )

            # ------------------------------------------
            # Show SQL
            # ------------------------------------------

            st.subheader(
                "Generated SQL"
            )

            st.code(
                sql,
                language="sql",
            )

            # ------------------------------------------
            # Validate SQL
            # ------------------------------------------

            validation = validate_sql(sql)

            if not validation.allowed:

                error = (
                    "SQL was not executed: "
                    f"{validation.reason}"
                )

                st.error(error)

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": error,
                        "sql": sql,
                        "error": error,
                    }
                )

                st.stop()

            # ------------------------------------------
            # Execute
            # ------------------------------------------

            with st.spinner(
                "Executing read-only query..."
            ):

                result = execute_query(
                    datasource_name=datasource_name,
                    sql=sql,
                )

            # ------------------------------------------
            # Result
            # ------------------------------------------

            if result.error:

                st.error(
                    result.error
                )

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "The query could not "
                            "be executed."
                        ),
                        "sql": sql,
                        "error": result.error,
                    }
                )

            else:

                st.success(
                    f"{result.row_count} rows returned "
                    f"in {result.execution_ms:.2f} ms"
                )

                st.dataframe(
                    result.rows,
                    use_container_width=True,
                )

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "Query executed successfully. "
                            f"{result.row_count} rows returned."
                        ),
                        "sql": sql,
                        "rows": result.rows,
                        "row_count": result.row_count,
                        "execution_ms": result.execution_ms,
                    }
                )

        except Exception as exc:

            error = str(exc)

            st.error(error)

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "The request could not "
                        "be completed."
                    ),
                    "error": error,
                }
            )