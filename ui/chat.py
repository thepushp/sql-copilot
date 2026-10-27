"""The Chat section."""

import streamlit as st

from ui.progress import Progress
from services.query_service import build_history
from ui.answering import answer_question
from ui.indexing import run_example
from ui.messages import render_message
from ui.state import is_indexed


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

            with st.status("Working...", expanded=True) as status:

                progress = Progress(status)

                reply = answer_question(
                    question,
                    history,
                    settings,
                    progress,
                )

                failed = bool(reply.get("error"))

                progress.update(
                    label="Failed" if failed else "Done",
                    state="error" if failed else "complete",
                    expanded=False,
                )

        st.session_state.messages.append(reply)

        st.rerun()
