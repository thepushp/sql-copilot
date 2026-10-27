"""The ER diagram section."""

import streamlit as st

from services.er_interactive import graph_data, viewer_height, viewer_html
from ui.common import current_theme as _theme, show_html
from ui.share import encode as encode_link, validate as validate_link
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


ER_VIEWS = ["Interactive", "Static (Graphviz)"]


ER_SHOW_LABELS = {
    "Around a table": "Around table",
    "Whole schema": "Schema",
    "Tables from last answer": "Last answer",
}


def _last_answer_tables():

    for message in reversed(st.session_state.messages):

        if message["role"] == "assistant" and message.get("tables"):

            return message["tables"]

    return []


def _write_link(mode, table, schema):
    """Keep the page URL in step with the diagram so it can be shared."""

    wanted = encode_link(
        mode,
        table,
        schema,
        st.session_state.get("er_depth"),
    )

    if dict(st.query_params) != wanted:

        st.query_params.from_dict(wanted)


def render_er_diagram():

    metadata = st.session_state.database_metadata

    if not metadata:

        st.info("Index the database to draw its relationships.")

        return

    index = table_index(metadata)

    # A shared link waits here until the database has been indexed.
    link = st.session_state.pop("pending_link", None)

    if link:

        for name, value in validate_link(
            link,
            set(index),
            {schema.name for schema in metadata.schemas},
        ).items():

            st.session_state[name] = value

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
    start = None
    schema = None

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

    _write_link(mode, start, schema)

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
