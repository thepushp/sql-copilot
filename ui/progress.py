"""Live progress for a chat answer: finished steps with timings, streamed SQL."""

import time

import streamlit as st


class Progress:
    """Wraps an st.status so existing `status.update(label=...)` calls log steps."""

    def __init__(self, status):

        self.status = status
        self.started = time.perf_counter()
        self.step_label = None
        self.step_started = self.started
        self.slot = None

    def _finish_step(self, now):

        if self.step_label is None:

            return

        with self.status:

            st.caption(f"✓ {self.step_label} · {now - self.step_started:.1f}s")

        self.step_label = None

    def update(self, label=None, state=None, expanded=None):

        now = time.perf_counter()

        if state is None and label is not None:

            self._finish_step(now)

            self.slot = None

            self.step_label = label.rstrip(".")
            self.step_started = now

            self.status.update(label=label, state="running", expanded=True)

            return

        self._finish_step(now)

        if state is not None:

            label = f"{label or 'Done'} · {now - self.started:.1f}s"

        self.status.update(
            label=label,
            state=state or "running",
            expanded=True if expanded is None else expanded,
        )

    def stream(self, text):
        """Show the SQL as the model writes it."""

        if self.slot is None:

            with self.status:

                self.slot = st.empty()

        self.slot.code(text, language="sql")
