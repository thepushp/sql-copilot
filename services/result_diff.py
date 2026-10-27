"""What changed between two runs of a query: the SQL text and the result rows."""

import difflib
from dataclasses import dataclass, field

import pandas as pd


MAX_SHOWN = 20


@dataclass
class ResultDiff:
    rows_before: int
    rows_after: int
    columns_added: list = field(default_factory=list)
    columns_removed: list = field(default_factory=list)
    added: pd.DataFrame = None
    removed: pd.DataFrame = None
    comparable: bool = True

    @property
    def same(self):

        return (
            not self.columns_added
            and not self.columns_removed
            and (self.added is None or self.added.empty)
            and (self.removed is None or self.removed.empty)
        )


def sql_diff(old, new):
    """Unified diff of two SQL texts, or an empty string when they match."""

    lines = difflib.unified_diff(
        old.strip().splitlines(),
        new.strip().splitlines(),
        lineterm="",
        n=1,
    )

    return "\n".join(line for line in lines if not line.startswith(("---", "+++")))


def _keyed(frame, columns):

    text = frame[columns].astype(str)

    return text.assign(_n=text.groupby(columns, dropna=False).cumcount())


def compare_results(old, new):
    """Row-level comparison on the columns both results share (order-insensitive)."""

    old_columns = list(old.columns)
    new_columns = list(new.columns)
    shared = [name for name in old_columns if name in new_columns]

    result = ResultDiff(
        rows_before=len(old),
        rows_after=len(new),
        columns_added=[name for name in new_columns if name not in old_columns],
        columns_removed=[name for name in old_columns if name not in new_columns],
    )

    if not shared:

        result.comparable = False

        return result

    left = _keyed(old, shared)
    right = _keyed(new, shared)

    both = left.merge(right, how="outer", indicator=True, on=list(left.columns))

    gone = both[both["_merge"] == "left_only"].drop(columns=["_merge", "_n"])
    new_rows = both[both["_merge"] == "right_only"].drop(columns=["_merge", "_n"])

    result.removed = gone.reset_index(drop=True)
    result.added = new_rows.reset_index(drop=True)

    return result
