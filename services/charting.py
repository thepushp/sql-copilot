import warnings
from datetime import date

import pandas as pd


MAX_CHART_ROWS = 200
MAX_BAR_ROWS = 40
MAX_SERIES = 3


def _as_numeric(series):

    if pd.api.types.is_bool_dtype(series):
        return None

    if pd.api.types.is_numeric_dtype(series):
        return series

    if series.dtype == object:

        converted = pd.to_numeric(series, errors="coerce")

        if converted.notna().sum() == series.notna().sum() and series.notna().any():
            return converted

    return None


def chart_data(dataframe):
    """Return (kind, frame) for a simple chart, or None if not chartable."""

    if dataframe is None or not 2 <= len(dataframe) <= MAX_CHART_ROWS:
        return None

    numeric = {}
    label = None

    for name in dataframe.columns:

        converted = _as_numeric(dataframe[name])

        if converted is not None:

            lowered = str(name).lower()

            if lowered == "id" or lowered.endswith("_id"):
                continue

            numeric[name] = converted

        elif label is None:

            label = name

    if label is None or not numeric:
        return None

    if dataframe[label].duplicated().any():
        return None

    frame = pd.DataFrame(numeric).iloc[:, :MAX_SERIES]

    frame.index = dataframe[label]

    is_time = pd.api.types.is_datetime64_any_dtype(dataframe[label]) or (
        dataframe[label].dtype == object
        and isinstance(dataframe[label].iloc[0], (pd.Timestamp,))
    )

    if not is_time:

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                is_time = pd.api.types.is_datetime64_any_dtype(
                    pd.to_datetime(dataframe[label], errors="raise")
                ) and not pd.api.types.is_numeric_dtype(dataframe[label])
        except (ValueError, TypeError):
            is_time = False

    if is_time:
        return "line", frame.sort_index()

    if len(frame) > MAX_BAR_ROWS:
        return None

    return "bar", frame


CHART_KINDS = ("Bar", "Horizontal bar", "Line", "Area", "Scatter", "Pie", "Histogram")
CHART_AGGREGATES = ("None", "Sum", "Average", "Count", "Min", "Max")
MAX_CATEGORIES = 30
MAX_SLICES = 12


def chartable_columns(dataframe):
    """(all columns, numeric columns) usable as chart axes."""

    numeric = [
        name for name in dataframe.columns
        if _as_numeric(dataframe[name]) is not None
    ]

    return list(dataframe.columns), numeric


def _is_temporal(series):

    if pd.api.types.is_datetime64_any_dtype(series):
        return True

    if series.dtype != object:
        return False

    first = series.dropna()

    return not first.empty and isinstance(first.iloc[0], (pd.Timestamp, date))


def prepare_chart(dataframe, x, y, kind="Bar", aggregate="None", color=None):
    """Tidy frame with columns x / y / series (and the kinds of x and y) for drawing."""

    for name in (x, y if kind != "Histogram" else x, color):
        if name is not None and name not in dataframe.columns:
            raise ValueError("Pick columns that exist in the data.")

    if kind == "Histogram":

        values = _as_numeric(dataframe[x])

        if values is None:
            raise ValueError(f"{x} is not numeric.")

        return pd.DataFrame({"x": values.dropna()}), "quantitative"

    values = _as_numeric(dataframe[y])

    if values is None and aggregate != "Count":
        raise ValueError(f"{y} is not numeric.")

    tidy = pd.DataFrame({"x": dataframe[x], "y": values if values is not None else dataframe[y]})

    if color is not None and color != x:
        tidy["series"] = dataframe[color].astype(str)

    numeric_x = _as_numeric(dataframe[x]) is not None
    temporal_x = _is_temporal(dataframe[x])

    if temporal_x:
        tidy["x"] = pd.to_datetime(tidy["x"], errors="coerce")
        x_type = "temporal"
    elif numeric_x and kind in ("Line", "Area", "Scatter"):
        tidy["x"] = _as_numeric(dataframe[x])
        x_type = "quantitative"
    else:
        tidy["x"] = tidy["x"].astype(str)
        x_type = "nominal"

    tidy = tidy.dropna(subset=["x"])

    if aggregate != "None":

        keys = [c for c in ("x", "series") if c in tidy.columns]
        how = {"Sum": "sum", "Average": "mean", "Count": "count", "Min": "min", "Max": "max"}[aggregate]
        tidy = tidy.groupby(keys, sort=False, dropna=False)["y"].agg(how).reset_index()

    if x_type == "nominal" and kind in ("Bar", "Horizontal bar", "Pie"):

        limit = MAX_SLICES if kind == "Pie" else MAX_CATEGORIES
        totals = tidy.groupby("x")["y"].sum().sort_values(ascending=False)
        tidy = tidy[tidy["x"].isin(totals.head(limit).index)]

    return tidy, x_type


def make_chart(dataframe, x, y, kind="Bar", aggregate="None", color=None):
    """An Altair chart with tooltips and readable axes."""

    import altair as alt

    tidy, x_type = prepare_chart(dataframe, x, y, kind, aggregate, color)

    if tidy.empty:
        raise ValueError("Nothing to chart.")

    y_title = f"{aggregate.lower()} of {y}" if aggregate not in ("None", "Count") else (
        f"count of {y}" if aggregate == "Count" else str(y)
    )
    has_series = "series" in tidy.columns
    series = alt.Color("series:N", title=str(color)) if has_series else alt.value("#4c78a8")

    if kind == "Histogram":

        return alt.Chart(tidy).mark_bar().encode(
            x=alt.X("x:Q", bin=alt.Bin(maxbins=30), title=str(x)),
            y=alt.Y("count()", title="rows"),
            tooltip=[alt.Tooltip("count()", title="rows")],
        )

    tooltip = [alt.Tooltip(f"x:{x_type[0].upper()}", title=str(x)), alt.Tooltip("y:Q", title=y_title, format=",.2f")]

    if has_series:
        tooltip.append(alt.Tooltip("series:N", title=str(color)))

    x_encoding = alt.X(f"x:{x_type[0].upper()}", title=str(x))
    y_encoding = alt.Y("y:Q", title=y_title)

    if kind == "Pie":

        return alt.Chart(tidy).mark_arc(innerRadius=50).encode(
            theta=alt.Theta("y:Q", stack=True),
            color=alt.Color("x:N", title=str(x), sort=alt.EncodingSortField("y", op="sum", order="descending")),
            tooltip=tooltip,
        )

    if kind == "Horizontal bar":

        return alt.Chart(tidy).mark_bar().encode(
            y=alt.Y("x:N", title=str(x), sort="-x" if has_series else alt.EncodingSortField("y", op="sum", order="descending")),
            x=alt.X("y:Q", title=y_title),
            color=series,
            tooltip=tooltip,
        )

    if kind == "Bar":

        bar_x = alt.X("x:N", title=str(x), sort=alt.EncodingSortField("y", op="sum", order="descending")) \
            if x_type == "nominal" else x_encoding

        return alt.Chart(tidy).mark_bar().encode(x=bar_x, y=y_encoding, color=series, tooltip=tooltip)

    if kind == "Scatter":
        return alt.Chart(tidy).mark_circle(size=60, opacity=0.7).encode(
            x=x_encoding, y=y_encoding, color=series, tooltip=tooltip,
        )

    mark = alt.Chart(tidy).mark_area(opacity=0.6, line=True) if kind == "Area" else alt.Chart(tidy).mark_line(point=True)

    return mark.encode(x=x_encoding, y=y_encoding, color=series, tooltip=tooltip)
