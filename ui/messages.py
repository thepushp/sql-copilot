"""Rendering one chat answer and the actions on it (approve, wrong, edit, follow-ups)."""

from sqlalchemy import text
import streamlit as st

from database.query_executor import execute_query
from database.sql_guard import classify_sql, validate_sql
from services.audit import audit
from services.exports import display_safe
from ui.common import NAV_ER
from ui.results import render_result
from ui.explorer import open_in_er_diagram
from services.followups import suggest_followups
from services.sql_qualify import format_sql
from services.result_diff import compare_results, sql_diff
from services.example_store import add_example, remove_example
from services.feedback_store import add_rejection, remove_rejection
from ui.answering import annotate
from ui.state import all_datasources


def approve_message(index):

    message = st.session_state.messages[index]

    add_example(
        message["datasource"],
        message["question"],
        message["sql"],
    )

    message["approved"] = True

    remove_rejection(message["datasource"], message["question"], message["sql"])

    audit(
        "approve",
        datasource=message["datasource"],
        question=message["question"],
        sql=message["sql"],
    )


MAX_SHOWN_DIFF = 20


FEEDBACK_REASONS = (
    "Wrong tables",
    "Wrong filter or value",
    "Wrong numbers (grouping or totals)",
    "Missing or extra columns",
    "Not what I asked",
    "Other",
)


def reject_message(index):
    """Thumbs down: remember why, and stop using the answer as an example."""

    message = st.session_state.messages[index]

    reason = st.session_state.get(f"feedback_reason_{index}") or FEEDBACK_REASONS[-1]
    note = (st.session_state.get(f"feedback_note_{index}") or "").strip()

    message["feedback"] = {"rating": "down", "reason": reason, "note": note}

    add_rejection(message["datasource"], message["question"], message["sql"], reason, note)

    if message.get("approved"):

        remove_example(message["datasource"], message["question"], message["sql"])

        message["approved"] = False

    audit(
        "feedback",
        datasource=message["datasource"],
        question=message["question"],
        sql=message["sql"],
        rating="down",
        reason=reason,
        note=note,
    )


def retry_with_feedback(index):

    message = st.session_state.messages[index]

    feedback = message["feedback"]

    detail = feedback["reason"] + (f": {feedback['note']}" if feedback["note"] else "")

    st.session_state.pending_question = (
        f"That answer was wrong ({detail}). Please try again: "
        f"{message['question']}"
    )


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

    if message.get("rows") is not None and message.get("sql"):

        message["previous"] = {
            "sql": message["sql"],
            "rows": message["rows"],
        }

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


def render_changes(message, index):
    """How the edited query differs from the one it replaced."""

    previous = message["previous"]
    dialect_key = message.get("dialect", "postgresql")

    before = format_sql(previous["sql"], dialect_key)
    after = format_sql(message["sql"], dialect_key)

    result = compare_results(previous["rows"], message["rows"])

    with st.expander("What changed since the previous run"):

        text = sql_diff(before, after)

        if text:

            st.code(text, language="diff")

        else:

            st.caption("The SQL is the same apart from formatting.")

        st.markdown(
            f"**Rows:** {result.rows_before:,} before, {result.rows_after:,} now"
        )

        if result.columns_added:

            st.markdown("**New columns:** " + ", ".join(result.columns_added))

        if result.columns_removed:

            st.markdown("**Removed columns:** " + ", ".join(result.columns_removed))

        if not result.comparable:

            st.caption("The two results share no columns, so rows cannot be compared.")

            return

        if result.same:

            st.caption("The rows are identical.")

        for label, frame in (("Rows added", result.added), ("Rows removed", result.removed)):

            if frame is not None and not frame.empty:

                st.markdown(f"**{label}** ({len(frame):,})")

                st.dataframe(
                    display_safe(frame.head(MAX_SHOWN_DIFF)),
                    hide_index=True,
                    width="stretch",
                )

                if len(frame) > MAX_SHOWN_DIFF:

                    st.caption(f"Showing the first {MAX_SHOWN_DIFF} of {len(frame):,}.")


def render_wrong_button(message, index):

    feedback = message.get("feedback")

    if feedback:

        st.button(
            "Marked wrong",
            icon=":material/thumb_down:",
            key=f"wrong_done_{index}",
            disabled=True,
            help=feedback["reason"],
        )

        st.button(
            "Try again",
            icon=":material/refresh:",
            key=f"retry_{index}",
            on_click=retry_with_feedback,
            args=(index,),
            help="Ask again, telling the model what was wrong.",
        )

        return

    with st.popover("Wrong", icon=":material/thumb_down:"):

        st.radio(
            "What was wrong?",
            FEEDBACK_REASONS,
            key=f"feedback_reason_{index}",
        )

        st.text_input(
            "Details (optional)",
            key=f"feedback_note_{index}",
            placeholder="e.g. should only count active students",
        )

        st.button(
            "Send feedback",
            key=f"feedback_send_{index}",
            on_click=reject_message,
            args=(index,),
            type="primary",
        )


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

            render_result(rows, key=f"result_{index}")

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

        has_rows = rows is not None and not rows.empty
        can_rate = bool(message.get("sql")) and not message.get("blocked") and (
            rows is not None
        )

        if message.get("previous") and rows is not None:

            render_changes(message, index)

        if has_rows or can_rate:

            with st.container(horizontal=True):

                if has_rows:

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
                        disabled=bool(message.get("approved")) or bool(message.get("feedback")),
                        help=(
                            "Save this question and SQL as a verified example "
                            "for future similar questions."
                        ),
                    )

                if can_rate:

                    render_wrong_button(message, index)

        details = []

        if message.get("example"):

            details.append("Verified example, answered without the model")

        if message.get("examples_used"):

            details.append(
                f"{message['examples_used']} approved example(s) used"
            )

        if message.get("rejections_used"):

            details.append(
                f"{message['rejections_used']} rejected answer(s) avoided"
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

        if message.get("steps"):

            with st.expander("How this was answered"):

                for label, seconds in message["steps"]:

                    st.caption(f"✓ {label} · {seconds:.1f}s")

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
