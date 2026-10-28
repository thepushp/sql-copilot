"""Helpful empty and error states: say what happened and what to do next."""

import streamlit as st

from services.error_hints import NO_SQL_TIPS, with_tips
from ui.common import NAV_CHAT
from ui.state import all_datasources


def show_error(text, kind="query"):
    """An error box with the original text and a short "What to try" list."""

    st.error(with_tips(text, kind))


def request_index():
    """Ask the sidebar to run indexing on the next run (it owns the settings)."""

    st.session_state["index_requested"] = True


def go_to_chat():

    st.session_state["nav"] = NAV_CHAT


def render_not_indexed(settings, purpose, key):
    """Why nothing can be shown yet, and a button that fixes the usual case."""

    name = settings.datasource_name

    if not all_datasources():

        st.warning(
            "**No database is set up yet.**\n\n"
            "1. Open **Add a connection** in the sidebar and fill in the details, then click **Add**.\n"
            "2. Or put them in a `.env` file (`DEV_DB_HOST`, `DEV_DB_NAME`, ... see `.env.example`) and restart the app.\n"
            "3. Then click **Discover & index database**."
        )

        return

    failed = st.session_state.get("index_error")

    if failed and failed["datasource"] == name:

        show_error(
            f"Could not read **{name}**: {failed['text']}",
            "database",
        )

    else:

        st.info(
            f"**{purpose}**\n\n"
            f"The app first reads the structure of **{name}** (tables and columns, "
            "never the data). That takes a few seconds."
        )

    st.button(
        "Index this database",
        icon=":material/search:",
        key=f"index_now_{key}",
        on_click=request_index,
        type="primary",
    )


def render_no_sql_tips():

    st.markdown("**What to try**\n" + "\n".join(f"- {tip}" for tip in NO_SQL_TIPS))


def example_hint(metadata):
    """A concrete question to try, from the biggest table."""

    tables = [
        table
        for schema in metadata.schemas
        for table in schema.tables
        if table.kind == "table"
    ] or [table for schema in metadata.schemas for table in schema.tables]

    if not tables:
        return None

    biggest = max(tables, key=lambda table: table.approx_rows or 0)

    return f"how many rows are in {biggest.name}?"
