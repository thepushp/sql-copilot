"""Result table and chart used by the chat answers and the Explorer."""

import pandas as pd
import streamlit as st

from database.query_executor import MAX_ROWS
from services.charting import (
    CHART_AGGREGATES,
    CHART_KINDS,
    chart_data,
    chartable_columns,
    make_chart,
)
from services.exports import display_safe


FILTER_MIN_ROWS = 8


def filter_rows(frame, query):
    """Rows where any cell contains every word of the query (case-insensitive)."""

    words = str(query or "").lower().split()

    if not words or frame.empty:

        return frame

    text = frame.astype(str).apply(lambda column: column.str.lower())
    joined = text.apply(" ".join, axis=1)
    mask = pd.Series(True, index=frame.index)

    for word in words:

        mask &= joined.str.contains(word, regex=False)

    return frame[mask]


def column_config(frame):
    """Thousands separators for numbers and a compact format for timestamps."""

    config = {}

    for name in frame.columns:

        column = frame[name]

        if pd.api.types.is_bool_dtype(column):

            continue

        if pd.api.types.is_numeric_dtype(column):

            config[name] = st.column_config.NumberColumn(format="localized")

        elif pd.api.types.is_datetime64_any_dtype(column):

            config[name] = st.column_config.DatetimeColumn(
                format="YYYY-MM-DD HH:mm:ss"
            )

    return config


def _suggestion(rows):
    """Chart type and axes the automatic chart would have used."""

    suggestion = chart_data(rows)

    if suggestion is None:

        return None, None, None

    kind, frame = suggestion

    return ("Line" if kind == "line" else "Bar"), frame.index.name, frame.columns[0]


def render_chart(frame, key, suggestion=(None, None, None)):
    """Chart controls (type, axes, aggregation, split) and the chart."""

    everything, numeric = chartable_columns(frame)

    if not numeric:

        st.caption("No numeric column to chart.")

        return

    kind_default, x_default, y_default = suggestion

    kind = st.segmented_control(
        "Type",
        CHART_KINDS,
        default=kind_default or CHART_KINDS[0],
        key=f"{key}_kind",
    ) or CHART_KINDS[0]

    with st.container(horizontal=True, vertical_alignment="bottom", wrap=True):

        x = st.selectbox(
            "X axis" if kind != "Histogram" else "Column",
            everything,
            index=everything.index(x_default) if x_default in everything else 0,
            key=f"{key}_x",
            width=180,
        )

        if kind != "Histogram":

            y = st.selectbox(
                "Y axis",
                numeric,
                index=numeric.index(y_default) if y_default in numeric else 0,
                key=f"{key}_y",
                width=180,
            )
            aggregate = st.selectbox(
                "Combine rows", CHART_AGGREGATES, key=f"{key}_agg", width=140
            )
            color = st.selectbox(
                "Split by", ["(none)"] + everything, key=f"{key}_color", width=180
            )

        else:

            y, aggregate, color = x, "None", "(none)"

    try:

        chart = make_chart(
            frame, x, y, kind, aggregate, None if color == "(none)" else color
        )

    except ValueError as exc:

        st.caption(str(exc))

        return

    st.altair_chart(chart, width="stretch")


def render_table(rows, key):

    shown = rows

    if len(rows) >= FILTER_MIN_ROWS:

        query = st.text_input(
            "Filter rows",
            key=f"{key}_filter",
            placeholder="Filter these rows...",
            label_visibility="collapsed",
        )

        shown = filter_rows(rows, query)

        if len(shown) != len(rows):

            st.caption(f"Showing {len(shown):,} of {len(rows):,} rows")

    st.dataframe(
        display_safe(shown),
        width="stretch",
        hide_index=True,
        column_config=column_config(shown),
    )

    if len(rows) >= MAX_ROWS:

        st.caption(
            f"Only the first {MAX_ROWS:,} rows are fetched. "
            "Add a filter or LIMIT to see specific rows."
        )


def render_result(rows, key="result"):
    """A table tab and, when something is numeric, a chart tab."""

    if rows.empty:

        st.info("The query returned no rows.")

        return

    _, numeric = chartable_columns(rows)

    if len(rows) < 2 or not numeric:

        render_table(rows, key)

        return

    table_tab, chart_tab = st.tabs(["Table", "Chart"])

    with table_tab:

        render_table(rows, key)

    with chart_tab:

        render_chart(rows, key, _suggestion(rows))
