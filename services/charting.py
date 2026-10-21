import warnings

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
