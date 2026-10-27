import pandas as pd

from services.result_diff import compare_results, sql_diff


def test_sql_diff_shows_changed_lines_only():
    old = "SELECT name\nFROM customer\nWHERE active"
    new = "SELECT name\nFROM customer\nWHERE active\nLIMIT 5"

    text = sql_diff(old, new)

    assert "+LIMIT 5" in text
    assert sql_diff(old, old) == ""


def test_rows_added_and_removed_ignore_order():
    old = pd.DataFrame({"n": ["a", "b", "c"], "v": [1, 2, 3]})
    new = pd.DataFrame({"n": ["c", "a", "d"], "v": [3, 1, 4]})

    diff = compare_results(old, new)

    assert (diff.rows_before, diff.rows_after) == (3, 3)
    assert list(diff.removed["n"]) == ["b"]
    assert list(diff.added["n"]) == ["d"]
    assert not diff.same


def test_identical_results_are_same():
    frame = pd.DataFrame({"n": [1, 2]})

    assert compare_results(frame, frame.copy()).same


def test_duplicate_rows_are_counted():
    old = pd.DataFrame({"n": [1, 1, 2]})
    new = pd.DataFrame({"n": [1, 2]})

    diff = compare_results(old, new)

    assert len(diff.removed) == 1 and diff.added.empty


def test_column_changes_compare_the_shared_columns():
    old = pd.DataFrame({"n": [1, 2], "x": [9, 9]})
    new = pd.DataFrame({"n": [1, 2], "y": [5, 5]})

    diff = compare_results(old, new)

    assert diff.columns_added == ["y"] and diff.columns_removed == ["x"]
    assert diff.added.empty and diff.removed.empty


def test_nothing_in_common():
    diff = compare_results(pd.DataFrame({"a": [1]}), pd.DataFrame({"b": [1]}))

    assert diff.comparable is False
