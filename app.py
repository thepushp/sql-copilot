import streamlit as st

from config.datasource_config import DATASOURCES
from config.model_config import MODEL_CONFIG

from database.database_inspector import (
    inspect_database,
)

from database.query_executor import (
    execute_query,
)

from database.sql_guard import (
    validate_sql,
)

from rag.context_builder import (
    build_context,
)

from rag.embeddings import (
    get_embeddings,
)

from rag.schema_indexer import (
    build_schema_index,
)

from rag.retriever import (
    create_retriever,
)

from services.llm_service import (
    get_llm,
)

from services.query_service import (
    process_question,
)


st.set_page_config(
    page_title="RAG DB Copilot",
    page_icon="🗄️",
    layout="wide",
)


if "messages" not in st.session_state:
    st.session_state.messages = []


if "database_metadata" not in st.session_state:
    st.session_state.database_metadata = None


if "schema_index" not in st.session_state:
    st.session_state.schema_index = None


if "indexed_datasource" not in st.session_state:
    st.session_state.indexed_datasource = None


with st.sidebar:

    st.header(
        "⚙️ Configuration"
    )

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
        0.0,
        1.0,
        0.1,
        0.1,
    )

    st.divider()

    if st.button(
        "🔄 Discover & Index Database",
        use_container_width=True,
    ):

        with st.spinner(
            "Discovering all accessible schemas..."
        ):

            try:

                metadata = inspect_database(
                    datasource_name
                )

                documents = (
                    metadata.to_documents()
                )

                index = build_schema_index(
                    documents,
                    get_embeddings(),
                )

                st.session_state.database_metadata = (
                    metadata
                )

                st.session_state.schema_index = (
                    index
                )

                st.session_state.indexed_datasource = (
                    datasource_name
                )

                st.success(
                    f"Indexed "
                    f"{metadata.schema_count} schemas, "
                    f"{metadata.table_count} tables "
                    f"and "
                    f"{metadata.view_count} views."
                )

            except Exception as exc:

                st.error(
                    f"Database discovery failed: {exc}"
                )

    if st.button(
        "🗑️ Clear Chat",
        use_container_width=True,
    ):

        st.session_state.messages = []

        st.rerun()


st.title(
    "🗄️ RAG Database Copilot"
)

st.caption(
    "Database-wide natural-language SQL assistant"
)


if (
    st.session_state.indexed_datasource
    != datasource_name
):

    st.info(
        "Click **Discover & Index Database** "
        "before asking questions."
    )


tab_chat, tab_explorer = st.tabs(
    [
        "💬 Chat",
        "🌳 Database Explorer",
    ]
)


with tab_explorer:

    metadata = (
        st.session_state.database_metadata
    )

    if not metadata:

        st.info(
            "Discover the database first."
        )

    else:

        st.write(
            f"**{metadata.schema_count} schemas** · "
            f"**{metadata.table_count} tables** · "
            f"**{metadata.view_count} views**"
        )

        for schema in metadata.schemas:

            with st.expander(
                f"📁 {schema.name}"
            ):

                for table in schema.tables:

                    st.markdown(
                        f"### 📋 {table.name}"
                    )

                    columns = []

                    for column in table.columns:

                        columns.append(
                            {
                                "Column": column.name,
                                "Type": column.data_type,
                                "Nullable": column.nullable,
                                "Default": column.default,
                                "PK": (
                                    "✓"
                                    if column.primary_key
                                    else ""
                                ),
                            }
                        )

                    st.dataframe(
                        columns,
                        use_container_width=True,
                        hide_index=True,
                    )

                    if table.foreign_keys:

                        st.caption(
                            "Foreign Keys"
                        )

                        for fk in (
                            table.foreign_keys
                        ):

                            st.write(
                                f"`{fk.column}` → "
                                f"`{fk.foreign_schema}."
                                f"{fk.foreign_table}."
                                f"{fk.foreign_column}`"
                            )

                    if table.indexes:

                        st.caption(
                            "Indexes"
                        )

                        for index in (
                            table.indexes
                        ):

                            st.write(
                                f"`{index.name}` — "
                                f"{', '.join(index.columns)}"
                            )


with tab_chat:

    for message in (
        st.session_state.messages
    ):

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

            if message.get(
                "blocked"
            ):

                st.warning(
                    "Execution blocked. "
                    "DML/DDL is review-only."
                )

            if message.get(
                "rows"
            ) is not None:

                st.dataframe(
                    message["rows"],
                    use_container_width=True,
                )

                st.caption(
                    f'{message["row_count"]} rows · '
                    f'{message["execution_ms"]:.2f} ms'
                )

            if message.get(
                "error"
            ):

                st.error(
                    message["error"]
                )


    question = st.chat_input(
        "Ask anything about the database..."
    )


    if question:

        st.session_state.messages.append(
            {
                "role": "user",
                "content": question,
            }
        )

        with st.chat_message(
            "user"
        ):

            st.markdown(
                question
            )


        with st.chat_message(
            "assistant"
        ):

            if not st.session_state.schema_index:

                st.warning(
                    "Discover and index the "
                    "database first."
                )

                st.stop()


            try:

                with st.spinner(
                    "Finding relevant database objects..."
                ):

                    retriever = (
                        create_retriever(
                            st.session_state.schema_index
                        )
                    )

                    documents = (
                        retriever.invoke(
                            question
                        )
                    )

                    context = (
                        build_context(
                            documents
                        )
                    )


                with st.spinner(
                    "Generating SQL..."
                ):

                    llm = get_llm(
                        provider=provider,
                        model=model,
                        temperature=temperature,
                    )

                    response = (
                        process_question(
                            llm=llm,
                            question=question,
                            context=context,
                        )
                    )


                st.subheader(
                    "Generated SQL"
                )

                st.code(
                    response.sql,
                    language="sql",
                )


                if response.operation != "READ":

                    st.warning(
                        f"Detected operation: "
                        f"**{response.operation}**"
                    )

                    st.info(
                        "This SQL is available for "
                        "review/copying only. "
                        "DML and DDL are never "
                        "executed."
                    )

                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                f"Generated "
                                f"{response.operation} "
                                "SQL. Execution was blocked."
                            ),
                            "sql": response.sql,
                            "blocked": True,
                        }
                    )

                    st.stop()


                validation = validate_sql(
                    response.sql
                )


                if not validation.allowed:

                    st.error(
                        validation.reason
                    )

                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "The generated SQL "
                                "failed the read-only "
                                "SQL guard."
                            ),
                            "sql": response.sql,
                            "error": validation.reason,
                        }
                    )

                    st.stop()


                with st.spinner(
                    "Executing query..."
                ):

                    result = execute_query(
                        datasource_name,
                        response.sql,
                    )


                if result.error:

                    st.error(
                        result.error
                    )

                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "The query failed."
                            ),
                            "sql": response.sql,
                            "error": result.error,
                        }
                    )

                else:

                    st.success(
                        f"{result.row_count} rows "
                        f"returned in "
                        f"{result.execution_ms:.2f} ms"
                    )

                    st.dataframe(
                        result.rows,
                        use_container_width=True,
                    )

                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "Query executed successfully."
                            ),
                            "sql": response.sql,
                            "rows": result.rows,
                            "row_count": result.row_count,
                            "execution_ms": (
                                result.execution_ms
                            ),
                        }
                    )


            except Exception as exc:

                st.error(
                    str(exc)
                )

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "The request could "
                            "not be completed."
                        ),
                        "error": str(exc),
                    }
                )