"""Quick per-column statistics for a page of rows."""

import pandas as pd


def _short(value, width=40):

    text = str(value)

    return text if len(text) <= width else text[: width - 1] + "…"


def profile(frame):
    """One row per column: type, how many values, nulls, distinct, min, max."""

    rows = []

    for name in frame.columns:

        column = frame[name]

        try:
            distinct = column.nunique(dropna=True)
        except TypeError:
            distinct = column.astype(str).nunique(dropna=True)

        low = high = ""

        try:
            if column.notna().any():
                low, high = _short(column.min()), _short(column.max())
        except TypeError:
            as_text = column.dropna().astype(str)
            if not as_text.empty:
                low, high = _short(as_text.min()), _short(as_text.max())

        rows.append(
            {
                "Column": name,
                "Type": str(column.dtype),
                "Values": int(column.notna().sum()),
                "Nulls": int(column.isna().sum()),
                "Distinct": int(distinct),
                "Min": low,
                "Max": high,
            }
        )

    return pd.DataFrame(rows)
