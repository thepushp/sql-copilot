"""Sidebar section: past questions of this session, pin, re-run, export."""

import streamlit as st

from database.query_executor import execute_query
from database.sql_guard import classify_sql, validate_sql
from services import history
from services.audit import audit
from ui.common import NAV_CHAT


MAX_SHOWN = 30


def _pins():

    if "history_pins" not in st.session_state:
        st.session_state.history_pins = history.load_pins()

    return st.session_state.history_pins


def _toggle(item):

    st.session_state.history_pins = history.toggle_pin(_pins(), item)

    history.save_pins(st.session_state.history_pins)


def _ask_again(item):

    st.session_state.pending_question = item.question
    st.session_state.nav = NAV_CHAT


def rerun_sql(item, config, dialect_key, policy):
    """Re-run stored SQL through the same guard and executor. Returns an error text or None."""

    sql = item.sql.strip().rstrip(";").strip()

    try:

        operation = classify_sql(sql, dialect_key)

        if operation != "READ":
            return f"Only SELECT queries can be re-run (this is {operation})."

        validation = validate_sql(sql, policy, dialect_key)

        if not validation.allowed:
            return f"Rejected by the safety guard: {validation.reason}"

    except Exception as exc:

        return str(exc)

    result = execute_query(item.datasource, sql, config)

    audit(
        "history_rerun",
        datasource=item.datasource,
        dialect=dialect_key,
        question=item.question,
        sql=sql,
        outcome="error" if result.error else "ok",
        rows=result.row_count,
        elapsed_ms=round(result.execution_ms),
        error=result.error,
    )

    st.session_state.messages.append(
        {"role": "user", "content": item.question},
    )

    message = {
        "role": "assistant",
        "question": item.question,
        "datasource": item.datasource,
        "dialect": dialect_key,
        "sql": sql,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    if result.error:

        message["error"] = f"The query failed: {result.error}"

    else:

        message["rows"] = result.rows
        message["row_count"] = result.row_count
        message["content"] = (
            f"**{result.row_count} rows** in {result.execution_ms:.0f} ms"
        )

    st.session_state.messages.append(message)

    st.session_state.nav = NAV_CHAT

    return None


def render_history(datasource_name, config, dialect_key):

    items = history.merge(
        history.collect(st.session_state.messages, datasource_name),
        _pins(),
        datasource_name,
    )

    with st.expander(f"Query history ({len(items)})", expanded=False):

        if not items:

            st.caption("Questions you ask appear here.")

            return

        error = st.session_state.pop("history_error", None)

        if error:
            st.error(error)

        for position, item in enumerate(items[:MAX_SHOWN]):

            st.markdown(
                f"{':pushpin: ' if item.pinned else ''}{item.question}",
            )

            with st.container(horizontal=True):

                st.button(
                    "Unpin" if item.pinned else "Pin",
                    key=f"hist_pin_{position}",
                    on_click=_toggle,
                    args=(item,),
                )

                st.button(
                    "Ask again",
                    key=f"hist_ask_{position}",
                    on_click=_ask_again,
                    args=(item,),
                )

                if item.sql and st.button(
                    "Re-run SQL",
                    key=f"hist_run_{position}",
                    help="Runs the stored SQL again through the safety guard.",
                ):

                    error = rerun_sql(
                        item,
                        config,
                        dialect_key,
                        st.session_state.access_policy,
                    )

                    if error:
                        st.session_state.history_error = error

                    st.rerun()

        st.caption("Exports contain questions and SQL only, never results.")

        with st.container(horizontal=True):

            st.download_button(
                "JSON",
                history.to_json(items),
                file_name="query_history.json",
                mime="application/json",
                key="hist_json",
            )

            st.download_button(
                "CSV",
                history.to_csv(items),
                file_name="query_history.csv",
                mime="text/csv",
                key="hist_csv",
            )
