"""The Explorer: a navigator with search on the left, a table page on the right.

A table page has four sections (Properties, Data, Diagram, SQL). Only the
active section does any work, so opening a table never runs a query by itself.
"""

import hashlib

import pandas as pd
import streamlit as st

from database.query_executor import MAX_ROWS, execute_query
from database.sql_guard import classify_sql, validate_sql
from services.audit import audit
from services.charting import chartable_columns
from ui.results import render_chart
from services.column_profile import (
    SAMPLE_ROWS,
    SPREAD_ROWS,
    TOP_VALUES,
    is_sampled,
    kind_of,
    profile_queries,
    spread_frame,
    summarize,
)
from services.data_profile import profile
from services.er_diagram import (
    MAX_TABLES,
    build_dot,
    build_mermaid,
    expand_around,
    most_connected,
    table_index,
)
from services.er_interactive import graph_data, viewer_height, viewer_html
from services.exports import EXPORT_ROW_LIMIT, build_exports, display_safe
from services.schema_search import search_metadata
from services.query_plan import explain
from services.sql_builders import (
    count_query,
    displayable_columns,
    equals_filter,
    page_query,
    parse_filter,
    table_key,
)
from services.sql_qualify import format_sql
from services.sql_templates import generate_templates, group_template
from services.table_info import column_targets, incoming, outgoing
from ui.common import NAV_ER, current_theme, show_html
from ui.results import render_result


SECTIONS = ["Properties", "Data", "Diagram", "SQL"]
PAGE_SIZES = [25, 50, 100, 200]
RECENT_LIMIT = 8
LIST_VIEWS = ["All", "Starred", "Recent"]
NAV_LIMIT = 80
GROUP_LABEL = "Count rows per column…"
DEFAULT_ORDER = "(default order)"

# state that belongs to one table's page and is cleared when another is opened
_PAGE_STATE = (
    "data_filter", "data_sort", "data_desc", "data_page", "data_page_size",
    "_data_cache", "_data_count", "_export", "sql_template", "sql_group",
    "sql_result", "table_depth", "data_columns", "sql_free", "sql_free_result",
    "_plan",
)


def format_rows(count):

    if count is None or count <= 0:
        return ""

    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".replace(".0M", "M")

    if count >= 1_000:
        return f"{count / 1_000:.1f}k".replace(".0k", "k")

    return str(count)


def format_bytes(size):

    if not size or size <= 0:
        return ""

    for unit in ("bytes", "KB", "MB", "GB", "TB"):

        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}".replace(".0 ", " ")

        size /= 1024


# ---------------------------------------------------------------- selection

def select_table(key):
    """Open a table; clear what belonged to the previous one."""

    st.session_state.explorer_selected = key

    for name in _PAGE_STATE:
        st.session_state.pop(name, None)

    recent = [k for k in st.session_state.get("explorer_recent", []) if k != key]

    st.session_state["explorer_recent"] = ([key] + recent)[:RECENT_LIMIT]


def toggle_favorite(key):

    starred = list(st.session_state.get("explorer_favorites", []))

    if key in starred:
        starred.remove(key)
    else:
        starred.append(key)

    st.session_state["explorer_favorites"] = starred


def open_row(table_key, column, value, dialect_key):
    """Jump to a table's Data section, filtered to one value of a column."""

    select_table(table_key)

    st.session_state["table_section"] = "Data"
    st.session_state["data_filter"] = equals_filter(column, value, dialect_key)


def open_in_er_diagram(key):

    st.session_state["er_mode"] = "Around a table"
    st.session_state["er_table"] = key
    st.session_state["nav"] = NAV_ER


def _ensure_selection(index):

    selected = st.session_state.get("explorer_selected")

    if selected not in index:
        select_table(most_connected(index) or next(iter(index)))


# ---------------------------------------------------------------- navigator

def _entry_label(table, key, hint=None):
    """Search results: schema.table and, under it, why it matched. Otherwise a single line."""

    if hint:
        return f"{key}  \n:gray[{hint}]"

    detail = table.kind if table.kind != "table" else (
        f"~{format_rows(table.approx_rows)}" if format_rows(table.approx_rows) else ""
    )

    return f"{table.name}  ·  :gray[{detail}]" if detail else table.name


def render_navigator(metadata, index):

    st.text_input(
        "Search",
        placeholder="Search tables, columns…",
        help="Searches table names, column names, comments and known values in every schema.",
        key="explorer_search",
        label_visibility="collapsed",
    )

    query = st.session_state.explorer_search.strip()

    if query:

        hits = search_metadata(metadata, query, limit=NAV_LIMIT)

        entries = [(hit.key, hit.summary()) for hit in hits]

        st.caption(
            f"{len(hits)} match{'es' if len(hits) != 1 else ''} in all schemas"
            if hits else "Nothing matches. Try fewer or shorter words."
        )

    else:

        mode = st.segmented_control(
            "List", LIST_VIEWS, default="All", required=True,
            key="explorer_list", label_visibility="collapsed",
        )

        if mode == "All":

            names = [schema.name for schema in metadata.schemas]

            biggest = max(metadata.schemas, key=lambda s: len(s.tables)).name

            choice = st.selectbox(
                "Schema",
                names,
                key="explorer_schema",
                **({} if "explorer_schema" in st.session_state else {"index": names.index(biggest)}),
            )

            schema = next(s for s in metadata.schemas if s.name == choice)

            entries = [(table_key(t), None) for t in schema.tables]

            st.caption(f"{len(entries)} in {choice}")

        else:

            wanted = st.session_state.get(
                "explorer_favorites" if mode == "Starred" else "explorer_recent", []
            )

            entries = [(k, index[k].schema) for k in wanted if k in index]

            st.caption(
                f"{len(entries)} {mode.lower()}"
                if entries
                else (
                    "Star a table with the star next to its name."
                    if mode == "Starred"
                    else "Tables you open appear here."
                )
            )

    selected = st.session_state.explorer_selected

    with st.container(height=520, border=True, key="explorer_nav"):

        for key, hint in entries[:NAV_LIMIT]:

            st.button(
                _entry_label(index[key], key, hint),
                key=f"nav_{key}",
                help=key,
                type="primary" if key == selected else "secondary",
                width="stretch",
                on_click=select_table,
                args=(key,),
            )

        if len(entries) > NAV_LIMIT:
            st.caption(f"Showing the first {NAV_LIMIT}. Search to narrow down.")


# --------------------------------------------------------------- properties

def _profile_chart(profile_data, column, kind):

    import altair as alt

    if kind in ("numeric", "temporal"):

        frame = spread_frame(profile_data.get("spread"), kind)

        if frame.empty:
            return None

        if kind == "numeric":
            x = alt.X("value:Q", bin=alt.Bin(maxbins=30), title=column.name)
        else:
            x = alt.X("value:T", timeUnit="yearmonth", title=column.name)

        return alt.Chart(frame).mark_bar().encode(
            x=x, y=alt.Y("count()", title="rows"), tooltip=[alt.Tooltip("count()", title="rows")]
        )

    top = profile_data.get("top")

    if top is None or top.empty:
        return None

    top = top.assign(value=top["value"].astype(str))

    return alt.Chart(top).mark_bar().encode(
        y=alt.Y("value:N", sort="-x", title=column.name),
        x=alt.X("occurrences:Q", title="rows"),
        tooltip=["value", "occurrences"],
    )


def _run_profile(settings, table, column):
    """Run the profile queries through the safety check. Returns (data, errors)."""

    sampled = is_sampled(table)
    queries = profile_queries(table, column, settings.dialect_key, sampled)

    data = {"sampled": sampled}
    errors = []

    for name, sql in queries.items():

        result, error = _checked_run(
            settings, sql, SPREAD_ROWS if name == "spread" else TOP_VALUES + 1
        )

        if error:
            errors.append(f"{name}: {error}")
            continue

        data[name] = result.rows

    audit(
        "profile",
        datasource=settings.datasource_name,
        table=f"{table.schema}.{table.name}",
        column=column.name,
        sampled=sampled,
        errors=errors or None,
    )

    return data, errors


def render_column_profile(settings, table, key):

    columns = displayable_columns(table)

    if not columns:
        return

    st.markdown("**Profile a column**")

    with st.container(horizontal=True, vertical_alignment="bottom", wrap=True):

        name = st.selectbox(
            "Column",
            [c.name for c in columns],
            key="profile_column",
            label_visibility="collapsed",
            width=240,
        )

        clicked = st.button(
            "Profile",
            icon=":material/analytics:",
            key="profile_run",
            help="Reads the column to count nulls, distinct values, range and common values.",
        )

    column = next(c for c in columns if c.name == name)

    cache = st.session_state.setdefault("_profiles", {})
    cache_key = (settings.datasource_name, key, name)

    if clicked:

        with st.spinner("Profiling..."):
            cache[cache_key] = _run_profile(settings, table, column)

    if cache_key not in cache:

        if is_sampled(table):
            st.caption(
                f"Large table: profiling uses its first {SAMPLE_ROWS:,} rows."
            )

        return

    data, errors = cache[cache_key]

    for text in errors:
        st.warning(text)

    summary = summarize(data.get("stats"))

    if not summary:
        return

    if data.get("sampled"):
        st.caption(f"Based on the first {SAMPLE_ROWS:,} rows of a large table.")

    def show(value):

        return "" if value is None or pd.isna(value) else str(value)[:60]

    cells = st.columns(3)

    cells[0].metric("Rows", f"{summary['rows']:,}")
    cells[1].metric("Nulls", f"{summary['nulls']:,} ({summary['null_share']:.1%})")
    cells[2].metric("Distinct", "n/a" if summary["distinct"] is None else f"{summary['distinct']:,}")

    if show(summary["min"]) or show(summary["max"]):

        st.markdown(f"**Range:** `{show(summary['min'])}` to `{show(summary['max'])}`")

    chart = _profile_chart(data, column, kind_of(column))

    left, right = st.columns([1, 1]) if chart is not None else (None, st.container())

    if chart is not None:

        with left:
            st.altair_chart(chart, width="stretch")

    top = data.get("top")

    present = summary["rows"] - summary["nulls"]

    if summary["distinct"] is not None and present and summary["distinct"] == present:

        with right:
            st.caption("Every value is unique.")

    elif (
        top is not None
        and not top.empty
        and (kind_of(column) == "other" or (summary["distinct"] or 0) <= TOP_VALUES * 3)
    ):

        with right:

            share = top["occurrences"] / max(summary["rows"] - summary["nulls"], 1)

            st.dataframe(
                display_safe(
                    top.assign(share=share).rename(
                        columns={"value": "Common value", "occurrences": "Rows", "share": "Share"}
                    )
                ),
                hide_index=True,
                width="stretch",
                column_config={
                    "Share": st.column_config.NumberColumn(format="percent"),
                    "Rows": st.column_config.NumberColumn(format="localized"),
                },
            )



def render_properties(settings, table, key, index):

    targets = column_targets(table, index)

    st.dataframe(
        [
            {
                "Column": column.name,
                "Type": column.data_type,
                "Null": "yes" if column.nullable == "YES" else "no",
                "Default": column.default or "",
                "Key": "PK" if column.primary_key else ("FK" if column.name in targets else ""),
                "References": targets.get(column.name, ""),
                "Values": ", ".join(column.values),
                "Description": column.comment or "",
            }
            for column in table.columns
        ],
        hide_index=True,
        width="stretch",
    )

    if table.definition:

        with st.expander("Definition", expanded=table.kind != "table"):

            st.code(format_sql(table.definition, settings.dialect_key), language="sql")

    render_column_profile(settings, table, key)

    references = outgoing(table, index)
    referenced_by = incoming(key, index)

    left, right = st.columns(2)

    with left:

        st.markdown(f"**References** ({len(references)})")

        if not references:
            st.caption("This table does not point to other tables.")

        for number, ref in enumerate(references):
            st.button(
                f"→ {ref.other_key.split('.', 1)[1]}   ({ref.column} → {ref.other_column})"
                + ("  · inferred" if ref.inferred else ""),
                key=f"ref_out_{number}",
                width="stretch",
                on_click=select_table,
                args=(ref.other_key,),
            )

    with right:

        st.markdown(f"**Referenced by** ({len(referenced_by)})")

        if not referenced_by:
            st.caption("No other table points to this one.")

        for number, ref in enumerate(referenced_by[:30]):
            st.button(
                f"← {ref.other_key.split('.', 1)[1]}   ({ref.other_column} → {ref.column})"
                + ("  · inferred" if ref.inferred else ""),
                key=f"ref_in_{number}",
                width="stretch",
                on_click=select_table,
                args=(ref.other_key,),
            )

        if len(referenced_by) > 30:
            st.caption(f"…and {len(referenced_by) - 30} more.")

    if table.indexes:

        st.markdown(f"**Indexes** ({len(table.indexes)})")

        st.dataframe(
            [
                {
                    "Name": i.name,
                    "Columns": ", ".join(i.columns),
                    "Unique": "yes" if i.unique else "",
                    "Method": i.method,
                }
                for i in table.indexes
            ],
            hide_index=True,
            width="stretch",
        )

    if table.constraints:

        st.markdown(f"**Constraints** ({len(table.constraints)})")

        st.dataframe(
            [
                {"Name": c.name, "Type": c.kind, "Rule": c.definition}
                for c in table.constraints
            ],
            hide_index=True,
            width="stretch",
        )

    if table.flyway_managed:

        st.caption(
            "Flyway: "
            + ("active" if table.flyway_active else "dropped")
            + (f" · created by {table.flyway_created_by}" if table.flyway_created_by else "")
            + (f" · last migration {table.flyway_last_migration}" if table.flyway_last_migration else "")
        )


# --------------------------------------------------------------------- data

def _reset_page():

    st.session_state["data_page"] = 0
    st.session_state.pop("_data_cache", None)
    st.session_state.pop("_data_count", None)


def _goto_page(delta):

    st.session_state["data_page"] = max(0, st.session_state.get("data_page", 0) + delta)


def _refresh():

    st.session_state.pop("_data_cache", None)


def _checked_run(settings, sql, max_rows):
    """Run a read after the safety check. Returns (QueryResult | None, error)."""

    check = validate_sql(sql, st.session_state.access_policy, settings.dialect_key)

    if not check.allowed:
        return None, check.reason

    result = execute_query(
        settings.datasource_name, sql, settings.datasource_config, max_rows=max_rows
    )

    return (None, result.error) if result.error else (result, None)


def _current_query(settings, table, limit, offset):
    """(sql, error) for the filter, sort and page the person has chosen."""

    try:
        where = parse_filter(st.session_state.get("data_filter", ""), settings.dialect_key)
    except ValueError as exc:
        return None, str(exc)

    sort = st.session_state.get("data_sort", DEFAULT_ORDER)

    order = None if sort == DEFAULT_ORDER else [(sort, bool(st.session_state.get("data_desc")))]

    chosen = st.session_state.get("data_columns") or []

    shown = [c for c in displayable_columns(table) if c.name in chosen] or None

    return page_query(
        table, settings.dialect_key, columns=shown, where=where, order_by=order,
        limit=limit, offset=offset,
    ), None


def render_data(settings, table, key, index):

    columns = [c.name for c in displayable_columns(table)] or [c.name for c in table.columns]

    with st.container(horizontal=True, vertical_alignment="bottom", wrap=True):

        st.text_input(
            "Filter",
            key="data_filter",
            placeholder="status = 'OPEN' AND total > 100",
            help="A SQL condition (the part after WHERE). Press Enter to apply.",
            on_change=_reset_page,
            width=250,
        )

        st.selectbox(
            "Sort by", [DEFAULT_ORDER] + columns, key="data_sort",
            on_change=_reset_page, width=160,
        )

        st.toggle("Descending", key="data_desc", on_change=_reset_page)

        picked = st.session_state.get("data_columns") or []

        with st.popover(f"Columns ({len(picked)}/{len(columns)})" if picked else "Columns"):

            st.multiselect(
                "Show only these columns (none selected = all)",
                columns,
                key="data_columns",
                on_change=_reset_page,
            )

        st.selectbox(
            "Rows", PAGE_SIZES, index=1, key="data_page_size",
            on_change=_reset_page, width=90, help="Rows per page",
        )

    page_size = st.session_state.get("data_page_size", 50)
    page = st.session_state.get("data_page", 0)

    sql, error = _current_query(settings, table, page_size + 1, page * page_size)

    if error:
        st.error(error)
        return

    cache = st.session_state.get("_data_cache")

    if cache and cache["sql"] == sql and cache["ds"] == settings.datasource_name:
        result, error = cache["result"], None
    else:
        result, error = _checked_run(settings, sql, page_size + 1)
        st.session_state["_data_cache"] = {"sql": sql, "ds": settings.datasource_name, "result": result}
        audit(
            "browse",
            datasource=settings.datasource_name,
            dialect=settings.dialect_key,
            table=key,
            sql=sql,
            rows=None if result is None else min(result.row_count, page_size),
            elapsed_ms=None if result is None else round(result.execution_ms),
            error=error,
        )

    if error:
        st.error(error)
        return

    frame = result.rows
    has_next = len(frame) > page_size
    frame = frame.head(page_size)

    with st.container(horizontal=True, vertical_alignment="center"):

        st.button("", icon=":material/chevron_left:", on_click=_goto_page, args=(-1,), disabled=page == 0, key="data_prev", help="Previous page")
        st.button("", icon=":material/chevron_right:", on_click=_goto_page, args=(1,), disabled=not has_next, key="data_next", help="Next page")
        st.button("", icon=":material/refresh:", on_click=_refresh, key="data_refresh", help="Run the query again")

        st.caption(
            f"Rows {page * page_size + 1:,}–{page * page_size + len(frame):,}"
            if len(frame) else "No rows"
        )

        count = st.session_state.get("_data_count")

        if count and count["key"] == _count_key(settings, key):
            st.caption(count["error"] or f"{count['total']:,} rows match")
        elif not st.session_state.get("data_filter", "").strip() and table.approx_rows:
            st.caption(f"about {table.approx_rows:,} rows in the table")

        _render_count_and_export(settings, table, key)

    if frame.empty:
        st.info("No rows match." if page == 0 else "No more rows.")
        return

    event = st.dataframe(
        display_safe(frame),
        hide_index=True,
        width="stretch",
        height=min(36 * (len(frame) + 1) + 4, 560),
        on_select="rerun",
        selection_mode="single-row",
        key=grid_key(sql, settings.datasource_name),
    )

    picked_rows = event.selection.rows if event and event.selection else []

    if picked_rows and picked_rows[0] < len(frame):

        render_row_detail(
            settings, table, key, index, frame.iloc[picked_rows[0]],
            page * page_size + picked_rows[0] + 1,
        )

    else:

        st.caption("Select a row (checkbox on the left) to see it in full and follow its relationships.")

    render_plan(settings, sql, "data")

    with st.expander("Column statistics for this page"):
        st.dataframe(display_safe(profile(frame)), hide_index=True, width="stretch")

    _render_chart(frame)


def grid_key(sql, datasource):
    """A new selection state for every distinct query, page and sort."""

    return "data_grid_" + hashlib.md5(f"{datasource}|{sql}".encode()).hexdigest()[:12]


def _show(value):

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value)


def _usable(value):

    return bool(_show(value))


def _python_value(value):

    return value.item() if hasattr(value, "item") else value


def render_row_detail(settings, table, key, index, row, number):
    """One row laid out vertically, with buttons that follow its relationships."""

    with st.container(border=True):

        st.markdown(f"**Row {number:,}**")

        st.dataframe(
            pd.DataFrame(
                {"Column": list(row.index), "Value": [_show(v) for v in row.values]}
            ),
            hide_index=True,
            width="stretch",
            height=min(36 * (len(row) + 1) + 4, 420),
        )

        links = []

        for ref in outgoing(table, index):

            if ref.column in row.index and _usable(row[ref.column]):

                links.append(
                    (
                        f"Open {ref.other_key.split('.', 1)[1]} where {ref.other_column} = {_show(row[ref.column])}",
                        ref.other_key, ref.other_column, row[ref.column],
                        ":material/north_east:",
                    )
                )

        for ref in incoming(key, index)[:8]:

            if ref.column in row.index and _usable(row[ref.column]):

                links.append(
                    (
                        f"Rows of {ref.other_key.split('.', 1)[1]} where {ref.other_column} = {_show(row[ref.column])}",
                        ref.other_key, ref.other_column, row[ref.column],
                        ":material/south_west:",
                    )
                )

        if not links:
            return

        st.caption("Follow a relationship")

        with st.container(horizontal=True, wrap=True):

            for position, (label, other, column, value, icon) in enumerate(links):

                st.button(
                    label,
                    icon=icon,
                    key=f"follow_{position}_{other}_{column}",
                    on_click=open_row,
                    args=(other, column, _python_value(value), settings.dialect_key),
                )


def render_plan(settings, sql, scope):
    """How the database would run this query, planned but not executed."""

    with st.popover("Query plan", icon=":material/account_tree:"):

        if st.button("Show plan", key=f"plan_{scope}", help="Asks the database for its plan. The query is not run."):

            text, error = explain(
                settings.datasource_name, sql, settings.datasource_config, settings.dialect_key
            )

            audit(
                "plan", datasource=settings.datasource_name, dialect=settings.dialect_key,
                sql=sql, error=error,
            )

            st.session_state["_plan"] = {"sql": sql, "text": text, "error": error}

        saved = st.session_state.get("_plan")

        if saved and saved["sql"] == sql:

            if saved["error"]:
                st.warning(saved["error"])
            else:
                st.code(saved["text"], language="text")


def _render_chart(frame):

    if not chartable_columns(frame)[1]:
        return

    with st.expander("Chart this page"):
        render_chart(frame, "chart")


def _count_key(settings, key):

    return (settings.datasource_name, key, st.session_state.get("data_filter", "").strip())


def _render_count_and_export(settings, table, key):

    try:
        where = parse_filter(st.session_state.get("data_filter", ""), settings.dialect_key)
    except ValueError:
        return

    def count_rows():
        result, error = _checked_run(settings, count_query(table, settings.dialect_key, where), 1)
        st.session_state["_data_count"] = {
            "key": _count_key(settings, key),
            "total": None if error else int(result.rows.iloc[0, 0]),
            "error": error,
        }

    st.button(
        "Count rows", on_click=count_rows, key="data_count_button",
        help="Runs COUNT(*) with the current filter. Can be slow on very large tables.",
    )

    with st.popover("⤓ Export"):

        st.caption(
            f"Saves up to {EXPORT_ROW_LIMIT:,} rows using the current filter and sort."
        )

        def prepare():
            sql, error = _current_query(settings, table, EXPORT_ROW_LIMIT, 0)
            result, error = (None, error) if error else _checked_run(settings, sql, EXPORT_ROW_LIMIT)
            if error:
                st.session_state["_export"] = {"error": error}
                return
            frame = result.rows
            st.session_state["_export"] = {
                "files": build_exports(frame, f"{table.name}"),
                "rows": len(frame),
                "truncated": len(frame) >= EXPORT_ROW_LIMIT,
            }
            audit("export", datasource=settings.datasource_name, table=key,
                  rows=len(frame), sql=sql)

        st.button("Prepare files", on_click=prepare, key="export_prepare", width="stretch")

        ready = st.session_state.get("_export")

        if ready and ready.get("error"):
            st.error(ready["error"])

        elif ready:

            st.caption(
                f"{ready['rows']:,} rows"
                + (" (the limit was reached, so the table may have more)" if ready["truncated"] else "")
            )

            for label, filename, mime, data in ready["files"]:
                st.download_button(
                    label, data, file_name=filename, mime=mime, icon=":material/download:",
                    key=f"export_{label.lower()}", width="stretch",
                )


# ------------------------------------------------------------------ diagram

def render_diagram(settings, table, key, index):

    with st.container(horizontal=True, vertical_alignment="bottom"):

        depth = st.segmented_control(
            "How far to follow relationships",
            [1, 2],
            default=1,
            required=True,
            key="table_depth",
            format_func=lambda d: f"{d} step" + ("s" if d > 1 else ""),
        )

        st.button(
            "Open in the ER diagram section",
            on_click=open_in_er_diagram,
            args=(key,),
            key="open_in_er",
        )

    keys = expand_around(index, key, depth)[:MAX_TABLES]

    data = graph_data(
        index, keys, "Keys only", True, focus=key, theme=current_theme(),
        summary=f"{len(keys)} tables",
        exports={
            "mermaid": build_mermaid(index, keys, "Keys only", True),
            "dot": build_dot(index, keys, "Keys only", True),
        },
    )

    show_html(viewer_html(data), height=min(560, viewer_height(len(keys))))


# ---------------------------------------------------------------------- SQL

def render_sql(settings, table, key, index):

    template = _render_templates(settings, table, key, index)

    st.divider()

    render_free_query(settings, key, template.sql)


def _use_template(sql):

    st.session_state["sql_free"] = sql


def run_free_query(settings, key):

    sql = (st.session_state.get("sql_free") or "").strip().rstrip(";").strip()

    result, error = None, None

    try:

        operation = classify_sql(sql, settings.dialect_key)

        if operation != "READ":
            raise ValueError(f"Only SELECT queries can be run here (this is {operation}).")

        result, error = _checked_run(settings, sql, MAX_ROWS)

    except Exception as exc:

        error = str(exc)

    audit(
        "query", datasource=settings.datasource_name, dialect=settings.dialect_key,
        table=key, sql=sql, rows=None if result is None else result.row_count,
        elapsed_ms=None if result is None else round(result.execution_ms), error=error,
    )

    st.session_state["sql_free_result"] = {"sql": sql, "result": result, "error": error}


def render_free_query(settings, key, template_sql):
    """A free-form read-only editor; same guard and executor as everything else."""

    st.markdown("**Write your own query**")

    st.text_area(
        "Query",
        key="sql_free",
        height=140,
        placeholder="SELECT ...",
        label_visibility="collapsed",
    )

    with st.container(horizontal=True, vertical_alignment="center", wrap=True):

        st.button(
            "Run", icon=":material/play_arrow:", key="free_run",
            on_click=run_free_query, args=(settings, key), type="primary",
            help="Runs a single SELECT through the safety guard (up to 500 rows).",
        )

        st.button(
            "Use the generated SQL above", key="free_template",
            on_click=_use_template, args=(template_sql,),
        )

        sql = (st.session_state.get("sql_free") or "").strip().rstrip(";").strip()

        if sql:

            try:
                readable = classify_sql(sql, settings.dialect_key) == "READ"
            except Exception:
                readable = False

            if readable:
                render_plan(settings, sql, "free")

    st.caption("Read-only: only SELECT statements run. Hidden columns and excluded tables stay blocked.")

    saved = st.session_state.get("sql_free_result")

    if not saved:
        return

    if saved["error"]:

        st.error(saved["error"])

    else:

        result = saved["result"]

        st.caption(f"{result.row_count:,} rows · {result.execution_ms:.0f} ms")

        render_result(result.rows, key="free_result")


def _render_templates(settings, table, key, index):

    templates = generate_templates(table, index, settings.dialect_key)

    labels = [t.label for t in templates]

    labels.insert(2, GROUP_LABEL)

    choice = st.selectbox("Generate SQL", labels, key="sql_template")

    if choice == GROUP_LABEL:

        columns = displayable_columns(table) or table.columns

        picked = st.selectbox("Column", [c.name for c in columns], key="sql_group")

        template = group_template(
            table, next(c for c in columns if c.name == picked), settings.dialect_key
        )

    else:

        template = next(t for t in templates if t.label == choice)

    st.code(template.sql, language="sql")

    if template.note:
        st.caption(template.note)

    if not template.runnable:
        return template

    if st.button("Run", icon=":material/play_arrow:", key="sql_run"):

        result, error = _checked_run(settings, template.sql, 100)

        audit(
            "browse", datasource=settings.datasource_name, dialect=settings.dialect_key,
            table=key, sql=template.sql, rows=None if result is None else result.row_count,
            error=error,
        )

        st.session_state["sql_result"] = {"sql": template.sql, "result": result, "error": error}

    saved = st.session_state.get("sql_result")

    if saved and saved["sql"] == template.sql:

        if saved["error"]:
            st.error(saved["error"])
        else:
            st.dataframe(display_safe(saved["result"].rows), hide_index=True, width="stretch")
            st.caption(f"{saved['result'].row_count} rows · {saved['result'].execution_ms:.0f} ms")

    return template


# --------------------------------------------------------------------- page

def render_table_page(settings, index):

    key = st.session_state.explorer_selected
    table = index[key]

    with st.container(horizontal=True, vertical_alignment="center", gap="small"):

        st.subheader(table.name, width="content")

        starred = key in st.session_state.get("explorer_favorites", [])

        st.button(
            ":orange[★]" if starred else "☆",
            key="fav_toggle",
            on_click=toggle_favorite,
            args=(key,),
            type="tertiary",
            help="Remove from starred" if starred else "Star this table",
        )

    facts = [table.schema, table.kind]

    if table.approx_rows:
        facts.append(f"about {table.approx_rows:,} rows")

    if format_bytes(table.size_bytes):
        facts.append(format_bytes(table.size_bytes))

    facts.append(f"{len(table.columns)} columns")

    st.caption(" · ".join(facts))

    if table.comment:
        st.caption(table.comment)

    section = st.segmented_control(
        "Table section", SECTIONS, default="Properties", required=True,
        key="table_section", label_visibility="collapsed",
    )

    if section == "Data":
        render_data(settings, table, key, index)
    elif section == "Diagram":
        render_diagram(settings, table, key, index)
    elif section == "SQL":
        render_sql(settings, table, key, index)
    else:
        render_properties(settings, table, key, index)


def render_explorer(settings):

    metadata = st.session_state.database_metadata

    if not metadata:

        st.info("Index the database to browse its schema.")

        return

    index = table_index(metadata)

    _ensure_selection(index)

    navigator, page = st.columns([1.2, 3], gap="medium")

    with navigator:
        render_navigator(metadata, index)

    with page:
        render_table_page(settings, index)
