import csv
import io
import json

import pytest

from services import history
from test_app import Harness


def assistant(question, sql, datasource="Shop"):
    return {
        "role": "assistant",
        "question": question,
        "sql": sql,
        "datasource": datasource,
        "rows": [(1,)],
    }


class TestHistoryLogic:

    def test_collect_newest_first_dedup_and_datasource(self):
        messages = [
            {"role": "user", "content": "a"},
            assistant("a", "SELECT 1"),
            assistant("b", "SELECT 2"),
            assistant("a", "SELECT 1"),
            assistant("c", "SELECT 3", datasource="Other"),
        ]

        items = history.collect(messages, "Shop")

        assert [i.question for i in items] == ["a", "b"]

    def test_pin_toggle_and_merge_keeps_pins_after_clear(self):
        item = history.HistoryItem("Shop", "a", "SELECT 1")

        pins = history.toggle_pin([], item)
        assert len(pins) == 1

        merged = history.merge([], pins, "Shop")
        assert [(i.question, i.pinned) for i in merged] == [("a", True)]

        assert history.toggle_pin(pins, item) == []

    def test_pinned_first(self):
        items = history.collect(
            [assistant("a", "SELECT 1"), assistant("b", "SELECT 2")],
            "Shop",
        )
        pins = history.toggle_pin([], items[1])

        merged = history.merge(items, pins, "Shop")

        assert merged[0].pinned
        assert merged[0].question == "a"
        assert len(merged) == 2

    def test_exports_have_no_rows(self):
        items = [history.HistoryItem("Shop", 'q, "x"', "SELECT 1", True)]

        data = json.loads(history.to_json(items))
        assert set(data[0]) == {"datasource", "question", "sql", "pinned"}

        rows = list(csv.reader(io.StringIO(history.to_csv(items))))
        assert rows[1][1] == 'q, "x"'

    def test_persistence_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(history, "PERSIST", True)
        monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "h.json")

        pins = [{"datasource": "Shop", "question": "a", "sql": "SELECT 1"}]
        history.save_pins(pins)

        assert history.load_pins() == pins

    def test_no_disk_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(history, "PERSIST", False)
        monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "h.json")

        history.save_pins([{"datasource": "S", "question": "a"}])

        assert not (tmp_path / "h.json").exists()
        assert history.load_pins() == []


@pytest.mark.app
class TestHistoryUI:

    def test_sidebar_history_pin_and_rerun(self, env, monkeypatch):
        ui = Harness(monkeypatch, "SELECT COUNT(*) FROM customer").index()
        ui.ask("How many customers?")

        assert any(e.label.startswith("Query history (1)") for e in ui.at.sidebar.expander)

        ui.button(key="hist_pin_0").click().run()
        assert len(ui.at.session_state.history_pins) == 1

        before = len(ui.messages)
        ui.button(key="hist_run_0").click().run()
        assert not ui.at.exception, ui.at.exception

        assert len(ui.messages) == before + 2
        assert "error" not in ui.reply

    def test_rerun_rejects_non_read(self, env):
        from config.privacy import AccessPolicy
        from ui.history import rerun_sql

        item = history.HistoryItem("Shop", "q", "DELETE FROM customer")

        assert "Only SELECT" in rerun_sql(item, None, "sqlite", AccessPolicy())


def test_one_line_truncates():
    from ui.history import one_line

    assert one_line("short  text") == "short text"

    long = one_line("word " * 40)

    assert len(long) <= 34
    assert long.endswith("…")
    assert "\n" not in one_line("a\nb")
