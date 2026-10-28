"""The Chat section."""

import streamlit as st

from ui.progress import Progress
from services.query_service import build_history
from ui.answering import answer_question
from ui.hints import example_hint, render_not_indexed
from ui.indexing import run_example
from ui.messages import render_message
from ui.state import is_indexed


def render_chat(settings, busy):

    datasource_name = settings.datasource_name

    indexed = is_indexed(datasource_name)

    if busy:

        pass

    elif not st.session_state.messages and not indexed:

        render_not_indexed(
            settings,
            "Ask your database a question in plain English.",
            "chat",
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

            suggestion = example_hint(st.session_state.database_metadata)

            st.markdown(
                "**Ask a question about your database below.**"
                + (f" For example: *{suggestion}*" if suggestion else "")
            )

            st.caption(
                "No ready-made examples could be built for this database. "
                "Use the **Explorer** to see what tables and columns exist."
            )

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
        settings,
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

            st.warning(
                f"**No model is selected for {settings.provider}.**\n\n"
                "Open **Model & provider** in the sidebar and choose one. "
                "If the list is empty the provider could not be reached: "
                "for Ollama start it (`ollama serve`) and pull a model "
                "(`ollama pull <model>`); for hosted providers check the API key. "
                "You can also pick **Custom...** and type a model name."
            )

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

                reply["steps"] = list(progress.steps)

        st.session_state.messages.append(reply)

        st.rerun()
