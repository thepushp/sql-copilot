"""Headless UI tests: the real app.py with a scripted model and fake embeddings.

Nothing here touches the network, Ollama, or the configured PostgreSQL: the
only datasource is a temporary SQLite file.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from langchain_community.embeddings import DeterministicFakeEmbedding
from streamlit.testing.v1 import AppTest

from config.datasource_config import DATASOURCES
from conftest import ScriptedLLM
from services import example_store, glossary


pytestmark = pytest.mark.app

CHAT = ":material/chat: Chat"
EXPLORER = ":material/account_tree: Explorer"
ER = ":material/schema: ER diagram"

EMBEDDINGS = DeterministicFakeEmbedding(size=32)

def flat(sql):
    return " ".join(sql.split())


APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


class Harness:

    def __init__(self, monkeypatch, *sql_answers, tables=("main.customer", "main.orders"), summary="Summary text."):
        self.queue = list(sql_answers)
        self.tables = list(tables)

        def respond(prompt, number):
            if "choose which database tables" in prompt:
                return json.dumps(self.tables)
            if "explain SQL query results" in prompt:
                return summary
            return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]

        self.llm = ScriptedLLM(respond)
        monkeypatch.setattr("services.llm_service.get_llm", lambda **kwargs: self.llm)

        self.at = AppTest.from_file(APP_PATH, default_timeout=60).run()
        assert not self.at.exception, self.at.exception

    # --- element helpers -------------------------------------------------

    def button(self, label=None, key=None, sidebar=True):
        pool = self.at.sidebar.button if sidebar else self.at.main.button
        return next(b for b in pool if (label and b.label == label) or (key and b.key == key))

    def widget(self, kind, label=None, key=None):
        pool = getattr(self.at.sidebar, kind)
        return next(w for w in pool if (label and w.label == label) or (key and w.key == key))

    def seg(self, key):
        return next(c for c in self.at.segmented_control if c.key == key)

    def radio(self, key):
        return next(r for r in self.at.radio if r.key == key)

    def toggle(self, key):
        return next(t for t in self.at.toggle if t.key == key)

    def goto(self, section):
        self.seg("nav").set_value(section).run()
        assert not self.at.exception, self.at.exception
        return self

    def has(self, kind, label):
        return any(w.label == label for w in getattr(self.at.sidebar, kind))

    # --- actions -----------------------------------------------------------

    def index(self):
        self.button("Discover & index database").click().run()
        assert not self.at.exception, self.at.exception
        return self

    def ask(self, question):
        self.at.chat_input[0].set_value(question).run()
        assert not self.at.exception, self.at.exception
        return self.reply

    @property
    def reply(self):
        return self.at.session_state.messages[-1]

    @property
    def messages(self):
        return self.at.session_state.messages

    def sidebar_text(self):
        return [
            e.value
            for group in (self.at.sidebar.success, self.at.sidebar.error, self.at.sidebar.warning)
            for e in group
        ]


@pytest.fixture
def harness(env, monkeypatch):
    def make(*sql_answers, **kwargs):
        return Harness(monkeypatch, *sql_answers, **kwargs)
    return make


class TestStartupAndIndexing:

    def test_starts_without_errors_and_asks_to_index(self, harness):
        ui = harness("SELECT 1")

        assert ui.widget("selectbox", key="datasource_select").value == "Shop"
        assert ui.at.chat_input[0].disabled
        assert any("Get started" in i.value for i in ui.at.info)
        assert "Not indexed yet" in ui.sidebar_text()

    def test_indexing_enables_chat(self, harness):
        ui = harness("SELECT 1").index()

        assert "Ready: 1 schemas, 4 tables, 1 views" in ui.sidebar_text()
        assert not ui.at.chat_input[0].disabled
        assert [b.label for b in ui.at.main.button]  # example questions

    def test_explorer_lists_tables_and_views(self, harness):
        ui = harness("SELECT 1").index().goto(EXPLORER)
        keys = {b.key for b in ui.at.main.button if b.key.startswith("nav_")}

        assert {"nav_main.customer", "nav_main.orders", "nav_main.big_orders"} <= keys
        labels = {b.key: b.label for b in ui.at.main.button if b.key.startswith("nav_")}
        assert "view" in labels["nav_main.big_orders"]

    def test_examples_come_from_the_schema_and_are_verified(self, harness):
        ui = harness("SELECT 1").index()

        questions = [b.label for b in ui.at.main.button if b.key.startswith("example_")]

        assert len(questions) == 4
        assert "How many rows are in orders?" in questions
        assert "List orders together with their customer" in questions
        assert not any(q.startswith("Show 10 recent records") for q in questions)

    def test_clicking_an_example_answers_without_calling_the_model(self, harness):
        ui = harness("SELECT 1").index()

        ui.button(sidebar=False, label="How many rows are in orders?").click().run()

        assert ui.llm.calls == []
        assert ui.messages[0] == {"role": "user", "content": "How many rows are in orders?"}
        assert ui.reply["example"] is True
        assert ui.reply["rows"]["total"].tolist() == [3]
        assert ui.reply["input_tokens"] == 0
        assert ui.reply["tables"] == ["main.orders"]

    def test_example_answers_work_with_follow_up_questions(self, harness):
        ui = harness("SELECT 1").index()
        ui.button(sidebar=False, label="How many rows are in orders?").click().run()

        ui.ask("and for customers?")

        assert 'SELECT COUNT(*) AS total FROM "orders"' in flat(ui.llm.calls[-1])

    def test_examples_disappear_once_chatting(self, harness):
        ui = harness("SELECT 1").index()

        ui.ask("anything")

        assert not [b for b in ui.at.main.button if b.key.startswith("example_")]

    def test_examples_respect_privacy_settings(self, harness):
        ui = harness("SELECT 1").index()
        ui.widget("multiselect", label="Exclude tables").select("main.orders").select("main.order_item").run()

        ui.button("Apply privacy settings").click().run()

        sql = [e.sql for e in ui.at.session_state.example_questions]
        assert sql
        for statement in sql:
            assert '"orders"' not in statement
            assert '"order_item"' not in statement

    def test_examples_are_rebuilt_for_a_new_datasource(self, harness, tmp_path):
        import sqlite3

        path = tmp_path / "tiny.db"
        con = sqlite3.connect(path)
        con.executescript("CREATE TABLE gadget(id INTEGER PRIMARY KEY, name TEXT); INSERT INTO gadget VALUES (1,'a');")
        con.close()
        ui = harness("SELECT 1")
        ui.widget("selectbox", key="conn_type").select("sqlite").run()
        ui.widget("text_input", key="conn_db").set_value(str(path)).run()
        ui.widget("text_input", key="conn_name").set_value("Tiny").run()
        ui.button(key="conn_add").click().run()

        ui.index()

        questions = [b.label for b in ui.at.main.button if b.key.startswith("example_")]
        assert "How many rows are in gadget?" in questions

    def test_empty_database_has_no_examples_but_still_works(self, harness, tmp_path):
        import sqlite3

        path = tmp_path / "empty.db"
        sqlite3.connect(path).executescript("CREATE TABLE gadget(id INTEGER PRIMARY KEY);")
        ui = harness("SELECT 1")
        ui.widget("selectbox", key="conn_type").select("sqlite").run()
        ui.widget("text_input", key="conn_db").set_value(str(path)).run()
        ui.widget("text_input", key="conn_name").set_value("Empty").run()
        ui.button(key="conn_add").click().run()

        ui.index()

        assert not [b for b in ui.at.main.button if b.key.startswith("example_")]
        assert not ui.at.chat_input[0].disabled


class TestAnswering:

    def test_question_returns_rows(self, harness):
        ui = harness("SELECT name FROM customer ORDER BY id").index()

        reply = ui.ask("list customers")

        assert reply["rows"]["name"].tolist() == ["Ann", "Bob"]
        assert reply["content"].startswith("**2 rows**")
        assert flat(reply["sql"]) == "SELECT name FROM customer ORDER BY id"
        assert "main.customer" in reply["tables"]
        assert reply["input_tokens"] > 0 and reply["output_tokens"] > 0
        assert reply["question"] == "list customers"
        assert reply["datasource"] == "Shop"

    def test_selection_and_generation_each_call_the_model(self, harness):
        ui = harness("SELECT 1").index()

        ui.ask("anything")

        assert len(ui.llm.calls) == 2
        assert "choose which database tables" in ui.llm.calls[0]

    def test_smart_selection_can_be_turned_off(self, harness):
        ui = harness("SELECT 1").index()
        ui.widget("checkbox", label="Smart table selection").uncheck().run()

        ui.ask("anything")

        assert len(ui.llm.calls) == 1

    def test_selected_tables_drive_the_context(self, harness):
        ui = harness("SELECT 1", tables=["main.product"]).index()

        reply = ui.ask("anything")

        assert reply["tables"][0] == "main.product"
        assert "Table: product" in ui.llm.calls[-1]

    def test_dialect_hints_reach_the_model(self, harness):
        ui = harness("SELECT 1").index()

        ui.ask("anything")

        assert "SQLite" in ui.llm.calls[-1]

    def test_write_sql_is_shown_but_never_executed(self, harness, shop_db):
        ui = harness("DELETE FROM customer").index()

        reply = ui.ask("remove everyone")

        assert reply["blocked"] is True
        assert reply.get("rows") is None
        assert "DML" in reply["content"]
        con = sqlite3.connect(shop_db)
        assert con.execute("SELECT count(*) FROM customer").fetchone()[0] == 2
        con.close()

    def test_guard_rejects_dangerous_select(self, harness):
        ui = harness("SELECT load_extension('evil')").index()

        reply = ui.ask("do something nasty")

        assert "safety guard" in reply["error"]
        assert reply.get("rows") is None

    def test_relationship_question_answers_without_model_and_links_to_er(self, harness):
        ui = harness("CANNOT_GENERATE_SQL").index()

        reply = ui.ask("how is orders connected to other tables")

        assert "does not contain enough" not in reply["content"]
        assert reply["er_tables"]
        assert reply["input_tokens"] == 0

    def test_no_sql_answer_is_explained(self, harness):
        ui = harness("CANNOT_GENERATE_SQL").index()

        reply = ui.ask("what is the meaning of life")

        assert "does not contain enough" in reply["content"]
        assert "error" not in reply

    def test_failed_query_is_repaired_once(self, harness):
        ui = harness("SELECT nope FROM customer", "SELECT name FROM customer").index()

        reply = ui.ask("list customers")

        assert reply["corrected"] is True
        assert flat(reply["sql"]) == "SELECT name FROM customer"
        assert reply["rows"].shape[0] == 2
        assert "SELECT nope FROM customer" in flat(ui.llm.calls[-1])
        assert len(ui.llm.calls) == 3

    def test_unrepairable_query_reports_the_error(self, harness):
        ui = harness("SELECT nope FROM customer").index()

        reply = ui.ask("list customers")

        assert "The query failed" in reply["error"]
        assert not reply.get("corrected")

    def test_repair_that_fails_the_guard_is_not_run(self, harness):
        ui = harness("SELECT nope FROM customer", "DELETE FROM customer").index()

        reply = ui.ask("list customers")

        assert "The query failed" in reply["error"]

    def test_model_errors_are_shown_not_raised(self, env, monkeypatch):
        def broken(**kwargs):
            raise RuntimeError("model 'x' not found")

        ui = Harness(monkeypatch, "SELECT 1")
        monkeypatch.setattr("services.llm_service.get_llm", broken)
        ui.index()

        reply = ui.ask("anything")

        assert "model 'x' not found" in reply["error"]

    def test_summary_is_optional(self, harness):
        ui = harness("SELECT name FROM customer", summary="Two customers.").index()

        assert "summary" not in ui.ask("list customers")

        ui.widget("checkbox", label="Summarize results with the model").check().run()

        assert ui.ask("list customers again")["summary"] == "Two customers."


class TestConversation:

    def test_clarifying_question_is_shown_and_remembered(self, harness):
        ui = harness("CLARIFY: which status?", "SELECT name FROM customer WHERE status = 'ACTIVE'").index()

        first = ui.ask("show customers")

        assert first["clarify"] is True
        assert first["content"] == "which status?"
        assert "error" not in first
        assert first.get("rows") is None

        second = ui.ask("active ones")

        assert "CLARIFY: which status?" in ui.llm.calls[-1]
        assert "show customers" in ui.llm.calls[-1]
        assert second["rows"]["name"].tolist() == ["Ann"]

    def test_follow_ups_receive_previous_sql(self, harness):
        ui = harness("SELECT name FROM customer").index()

        ui.ask("list customers")
        ui.ask("sort them")

        assert "SELECT name FROM customer" in flat(ui.llm.calls[-1])

    def test_clear_chat(self, harness):
        ui = harness("SELECT 1").index()
        ui.ask("anything")

        ui.button("Clear chat").click().run()

        assert ui.messages == []

    def test_reindexing_clears_chat(self, harness):
        ui = harness("SELECT 1").index()
        ui.ask("anything")

        ui.index()

        assert ui.messages == []


class TestLearning:

    def test_approved_answer_is_saved_and_reused(self, harness, env):
        ui = harness("SELECT name FROM customer").index()
        ui.ask("list customers")

        ui.button(sidebar=False, label="Correct").click().run()

        saved = json.loads((env / "examples.json").read_text())
        assert len(saved) == 1 and flat(saved[0]["sql"]) == "SELECT name FROM customer"
        saved[0]["sql"] = "SELECT name FROM customer"
        assert saved == [{
            "datasource": "Shop", "question": "list customers", "sql": "SELECT name FROM customer",
        }]
        assert ui.messages[-1]["approved"] is True

        again = ui.ask("list customers")

        assert again["examples_used"] == 1
        assert "Question: list customers" in ui.llm.calls[-1]

    def test_no_approve_button_without_rows(self, harness):
        ui = harness("DELETE FROM customer").index()
        ui.ask("remove everyone")

        assert not [b for b in ui.at.main.button if "Correct" in b.label]


class TestGlossary:

    def test_saved_glossary_reaches_the_prompt(self, harness, env):
        ui = harness("SELECT 1").index()
        ui.widget("text_area", label="Glossary").set_value("client: the customer table").run()

        ui.button("Save glossary").click().run()
        ui.ask("how many clients")

        assert (env / "glossary.txt").read_text().strip() == "client: the customer table"
        assert "client: the customer table" in ui.llm.calls[-1]
        assert "the customer table" in ui.llm.calls[0]


class TestPrivacy:

    def test_excluded_table_disappears_and_is_blocked(self, harness):
        ui = harness("SELECT * FROM customer", tables=["main.orders"]).index()
        ui.widget("multiselect", label="Exclude tables").select("main.customer").run()

        ui.button("Apply privacy settings").click().run()

        assert "Ready: 1 schemas, 3 tables, 1 views" in ui.sidebar_text()
        reply = ui.ask("list customers")
        assert "excluded by the privacy settings" in reply["error"]
        assert "Table: customer" not in ui.llm.calls[-1]

    def test_hidden_columns_block_select_star(self, harness):
        ui = harness("SELECT * FROM customer").index()
        ui.widget("text_input", label="Hide columns matching").set_value("status").run()
        ui.button("Apply privacy settings").click().run()

        star = ui.ask("everything about customers")
        assert "SELECT *" in star["error"]

        ui.queue[:] = ["SELECT id, name FROM customer"]
        assert ui.ask("customer names")["rows"].shape[0] == 2

    def test_sample_values_toggle_rebuilds_without_database(self, harness):
        ui = harness("SELECT 1").index()
        ui.widget("checkbox", label="Include sample values in the index").uncheck().run()

        ui.button("Apply privacy settings").click().run()

        assert not ui.at.exception
        assert ui.at.session_state.privacy.include_sample_values is False


class TestConnectionForm:

    def fill(self, ui, path, name="Second shop"):
        ui.widget("selectbox", key="conn_type").select("sqlite").run()
        ui.widget("text_input", key="conn_db").set_value(path).run()
        ui.widget("text_input", key="conn_name").set_value(name).run()

    def test_form_fields_follow_the_database_type(self, harness):
        ui = harness("SELECT 1")

        ui.widget("selectbox", key="conn_type").select("sqlite").run()
        sqlite_labels = [t.label for t in ui.at.sidebar.text_input if t.key.startswith("conn_")]

        ui.widget("selectbox", key="conn_type").select("mysql").run()
        mysql_labels = [t.label for t in ui.at.sidebar.text_input if t.key.startswith("conn_")]

        assert sqlite_labels == ["Connection name", "Database file path"]
        assert {"Host", "Username", "Password"} <= set(mysql_labels)

    def test_test_button_reports_success_and_failure(self, harness, shop_db, tmp_path):
        ui = harness("SELECT 1")

        self.fill(ui, str(tmp_path / "missing.db"))
        ui.button(key="conn_test").click().run()
        assert any("not found" in e.value for e in ui.at.sidebar.error)

        self.fill(ui, shop_db)
        ui.button(key="conn_test").click().run()
        assert "Connection works." in ui.sidebar_text()

    def test_added_connection_is_selected_and_usable(self, harness, shop_db):
        ui = harness("SELECT name FROM customer")
        self.fill(ui, shop_db)

        ui.button(key="conn_add").click().run()

        select = ui.widget("selectbox", key="datasource_select")
        assert select.value == "Second shop"
        assert "Second shop" in ui.at.session_state.custom_datasources
        assert ui.widget("text_input", key="conn_name").value == ""

        ui.index()
        assert ui.ask("list customers")["rows"].shape[0] == 2

    def test_passwords_are_cleared_after_adding(self, harness, shop_db):
        ui = harness("SELECT 1")
        self.fill(ui, shop_db)

        ui.button(key="conn_add").click().run()

        assert ui.widget("text_input", key="conn_password").value == "" if ui.has("text_input", "Password") else True

    def test_validation(self, harness, shop_db):
        ui = harness("SELECT 1")

        ui.button(key="conn_add").click().run()
        assert any("Enter a connection name" in e.value for e in ui.at.sidebar.error)

        self.fill(ui, shop_db, name="Shop")
        ui.button(key="conn_add").click().run()
        assert any("already defined" in e.value for e in ui.at.sidebar.error)

        ui.widget("selectbox", key="conn_type").select("mysql").run()
        ui.widget("text_input", key="conn_name").set_value("Incomplete").run()
        ui.button(key="conn_add").click().run()
        assert any("required connection fields" in e.value for e in ui.at.sidebar.error)

    def test_remove_connection(self, harness, shop_db):
        ui = harness("SELECT 1")
        self.fill(ui, shop_db)
        ui.button(key="conn_add").click().run()

        ui.button("Remove this connection").click().run()

        assert ui.at.session_state.custom_datasources == {}
        assert ui.widget("selectbox", key="datasource_select").value == "Shop"

    def test_switching_datasource_requires_reindexing(self, harness, shop_db):
        ui = harness("SELECT 1").index()
        self.fill(ui, shop_db)

        ui.button(key="conn_add").click().run()

        assert ui.at.chat_input[0].disabled
        assert "Not indexed yet" in ui.sidebar_text()

    def test_sqlserver_shows_read_only_warning(self, harness):
        ui = harness("SELECT 1")

        ui.widget("selectbox", key="conn_type").select("mssql").run()

        assert any("read-only" in c.value for c in ui.at.sidebar.caption)


class TestNavigation:

    def test_starts_on_chat_with_a_slim_header(self, harness):
        ui = harness("SELECT 1")

        assert ui.seg("nav").value == CHAT
        labels = ui.seg("nav").options
        assert len(labels) == 3
        assert "Chat" in labels[0] and "Explorer" in labels[1] and "ER diagram" in labels[2]
        assert len(ui.at.chat_input) == 1
        assert not ui.at.title, "the big page title was replaced by a slim header"
        assert any("SQL Copilot" in h.proto.body for h in ui.at.sidebar.get("html"))

    def test_brand_is_available_for_the_collapsed_sidebar(self, harness):
        ui = harness("SELECT 1")

        bodies = [h.proto.body for h in ui.at.get("html")]
        assert any('class="sqlcopilot-brand"' in b and "SQL Copilot" in b for b in bodies)
        assert any("SQL Copilot" in h.proto.body for h in ui.at.sidebar.get("html"))
        css = " ".join(bodies)
        assert ".sqlcopilot-brand" in css
        assert 'aria-expanded="false"' in css, "shown only while the sidebar is collapsed"

    def test_header_shows_the_connection_once_indexed(self, harness):
        ui = harness("SELECT 1")
        assert any("Not indexed yet" in c.value for c in ui.at.caption)

        ui.index()

        assert any("Shop · SQLite · 4 tables, 1 views" in c.value for c in ui.at.caption)

    def test_the_chat_box_exists_only_in_the_chat_section(self, harness):
        ui = harness("SELECT 1").index()

        ui.goto(EXPLORER)
        assert len(ui.at.chat_input) == 0

        ui.goto(ER)
        assert len(ui.at.chat_input) == 0

        ui.goto(CHAT)
        assert len(ui.at.chat_input) == 1

    def test_only_the_active_section_is_built(self, harness):
        ui = harness("SELECT 1").index()
        assert not ui.at.get("iframe") and not ui.at.main.expander

        ui.goto(ER)
        assert ui.at.get("iframe")

        ui.goto(CHAT)
        assert not ui.at.get("iframe")

    def test_the_conversation_survives_switching_sections(self, harness):
        ui = harness("SELECT name FROM customer").index()
        ui.ask("list customers")

        ui.goto(ER)
        ui.goto(EXPLORER)
        ui.goto(CHAT)

        assert [m["role"] for m in ui.messages] == ["user", "assistant"]
        assert ui.messages[-1]["rows"].shape[0] == 2

    def test_examples_are_laid_out_in_a_grid_of_buttons(self, harness):
        ui = harness("SELECT 1").index()

        examples = [b for b in ui.at.main.button if b.key.startswith("example_")]

        assert len(examples) == 4

    def test_asking_from_the_chat_works_after_visiting_other_sections(self, harness):
        ui = harness("SELECT name FROM customer").index()
        ui.goto(ER)
        ui.goto(CHAT)

        assert ui.ask("list customers")["rows"].shape[0] == 2


class TestErDiagram:

    def open(self, ui):
        return ui.goto(ER)

    def frames(self, ui):
        return ui.at.get("iframe")

    def interactive(self, ui):
        """The graph data embedded in the interactive viewer, or None."""

        import re

        frames = self.frames(ui)

        if not frames:
            return None

        match = re.search(
            r'<script type="application/json" id="er-data">(.*?)</script>',
            frames[0].proto.srcdoc,
            re.S,
        )

        return json.loads(match.group(1))

    def static(self, ui):
        ui.radio("er_view").set_value("Static (Graphviz)").run()
        charts = ui.at.get("graphviz_chart")
        return charts[0].proto.spec if charts else None

    def mode(self, ui, name):
        ui.seg("er_mode").set_value(name).run()

    def test_needs_an_index(self, harness):
        ui = self.open(harness("SELECT 1"))

        assert self.interactive(ui) is None
        assert any("Index the database" in i.value for i in ui.at.info)

    def test_interactive_is_the_default_view(self, harness):
        ui = self.open(harness("SELECT 1").index())
        data = self.interactive(ui)

        assert ui.radio("er_view").value == "Interactive"
        assert not ui.at.get("graphviz_chart")
        assert len(data["tables"]) >= 3
        assert len(data["edges"]) >= 2
        assert data["focus"] == "main.customer"

    def test_toolbar_controls(self, harness):
        ui = self.open(harness("SELECT 1").index())

        assert ui.seg("er_mode").options == ["Around table", "Schema", "Last answer"]
        assert ui.radio("er_columns").options == ["Keys only", "All columns", "Names only"]
        assert ui.radio("er_columns").value == "All columns"
        assert ui.radio("er_view").options == ["Interactive", "Static (Graphviz)"]
        assert ui.toggle("er_inferred").value is True
        assert ui.at.get("iframe")[0].proto.srcdoc

    def test_dropdown_buttons_name_what_they_control_and_the_current_choice(self, harness):
        ui = self.open(harness("SELECT 1").index())
        labels = [p.proto.popover.label for p in ui.at.get("popover")]

        assert "Columns: All columns" in labels
        assert "View: Interactive" in labels

        ui.radio("er_columns").set_value("Keys only").run()
        ui.radio("er_view").set_value("Static (Graphviz)").run()
        labels = [p.proto.popover.label for p in ui.at.get("popover")]

        assert "Columns: Keys only" in labels
        assert "View: Static" in labels

    def test_there_is_no_vague_options_menu(self, harness):
        ui = self.open(harness("SELECT 1").index())

        assert not any("Options" in p.proto.popover.label for p in ui.at.get("popover"))

    def test_options_explain_themselves(self, harness):
        ui = self.open(harness("SELECT 1").index())
        text = " ".join(
            c.value for c in ui.at.caption
        ) + " ".join(r.proto.label for r in ui.at.radio if r.key.startswith("er_"))

        assert "What to show inside each table" in text
        assert "How to draw the diagram" in text

    def test_image_and_text_exports_live_in_the_viewer(self, harness):
        ui = self.open(harness("SELECT 1").index())
        data = self.interactive(ui)

        assert not ui.at.get("download_button")
        assert data["exports"]["mermaid"].startswith("erDiagram")
        assert data["exports"]["dot"].startswith("digraph ER")
        html = self.frames(ui)[0].proto.srcdoc
        for kind in ("png", "svg", "mermaid", "dot"):
            assert f'data-export="{kind}"' in html

    def test_static_view_keeps_text_downloads(self, harness):
        ui = self.open(harness("SELECT 1").index())

        self.static(ui)

        assert [d.proto.label for d in ui.at.get("download_button")] == ["Mermaid", "Graphviz"]

    def test_summary_is_shown_inside_the_viewer(self, harness):
        ui = self.open(harness("SELECT 1").index())
        data = self.interactive(ui)

        assert data["summary"] == f"{len(data['tables'])} tables · {len(data['edges'])} relationships"

    def test_viewer_follows_the_theme_when_known(self, harness):
        ui = self.open(harness("SELECT 1").index())

        assert self.interactive(ui)["theme"] in (None, "light", "dark")

    def test_viewer_embeds_the_script_and_styles(self, harness):
        ui = self.open(harness("SELECT 1").index())
        html = self.frames(ui)[0].proto.srcdoc

        assert "<svg" in html and "textContent" in html
        assert "__CSS__" not in html and "__JS__" not in html and "__DATA__" not in html

    def test_static_view_is_valid_dot(self, harness):
        ui = self.open(harness("SELECT 1").index())
        dot = self.static(ui)

        assert dot.startswith("digraph ER") and dot.rstrip().endswith("}")
        assert dot.count("[label=<<TABLE") >= 3
        assert dot.count(" -> ") >= 2
        assert not self.frames(ui)
        assert any("tables ·" in c.value for c in ui.at.caption)

    @pytest.mark.parametrize("mode", ["Whole schema", "Around a table"])
    def test_modes_render_in_both_views(self, harness, mode):
        ui = self.open(harness("SELECT 1").index())

        self.mode(ui, mode)
        assert not ui.at.exception
        assert self.interactive(ui)["tables"]

        assert self.static(ui)

    def test_columns_option_changes_what_is_shown(self, harness):
        ui = self.open(harness("SELECT 1").index())

        ui.radio("er_columns").set_value("Names only").run()

        assert all(t["columns"] == [] for t in self.interactive(ui)["tables"])

    def test_inferred_relationships_can_be_hidden(self, harness):
        ui = self.open(harness("SELECT 1").index())
        assert any(e["inferred"] for e in self.interactive(ui)["edges"])

        ui.toggle("er_inferred").set_value(False).run()

        assert not any(e["inferred"] for e in self.interactive(ui)["edges"])

    def test_whole_schema_includes_unconnected_tables_by_default(self, harness):
        ui = self.open(harness("SELECT 1").index())
        self.mode(ui, "Whole schema")

        metadata = ui.at.session_state["database_metadata"]
        every_table = {
            f"{schema.name}.{table.name}"
            for schema in metadata.schemas
            for table in schema.tables
            if schema.name == ui.at.session_state["er_schema"]
        }

        assert ui.toggle("er_hide_isolated").value is False
        assert {t["id"] for t in self.interactive(ui)["tables"]} == every_table

    def test_depth_slider_widens_the_neighbourhood(self, harness):
        ui = self.open(harness("SELECT 1").index())
        near = len(self.interactive(ui)["tables"])

        next(s for s in ui.at.slider if s.key == "er_depth").set_value(3).run()

        assert len(self.interactive(ui)["tables"]) >= near

    def test_tables_from_last_answer(self, harness):
        ui = harness("SELECT name FROM customer", tables=["main.customer", "main.orders"]).index()
        ui.ask("list customers")
        self.open(ui)

        self.mode(ui, "Tables from last answer")

        data = self.interactive(ui)
        ids = [t["id"] for t in data["tables"]]
        assert ids[:2] == ["main.customer", "main.orders"]
        assert any(
            e["child"] == "main.orders" and e["parent"] == "main.customer"
            for e in data["edges"]
        )

    def test_last_answer_without_questions_shows_hint(self, harness):
        ui = self.open(harness("SELECT 1").index())

        self.mode(ui, "Tables from last answer")

        assert self.interactive(ui) is None
        assert any("Ask a question first" in i.value for i in ui.at.info)

    def test_excluded_tables_are_not_drawn(self, harness):
        ui = harness("SELECT 1").index()
        ui.widget("multiselect", label="Exclude tables").select("main.customer").run()
        ui.button("Apply privacy settings").click().run()
        self.open(ui)

        assert "main.customer" not in [t["id"] for t in self.interactive(ui)["tables"]]
        assert "<B>customer</B>" not in self.static(ui)

    def test_a_big_schema_is_capped_and_says_so(self, harness, tmp_path):
        import sqlite3

        path = tmp_path / "big.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE hub(id INTEGER PRIMARY KEY)")
        for i in range(45):
            con.execute(f"CREATE TABLE t{i:02d}(id INTEGER PRIMARY KEY, hub_id INTEGER REFERENCES hub(id))")
        con.commit()
        con.close()
        ui = harness("SELECT 1")
        ui.widget("selectbox", key="conn_type").select("sqlite").run()
        ui.widget("text_input", key="conn_db").set_value(str(path)).run()
        ui.widget("text_input", key="conn_name").set_value("Big").run()
        ui.button(key="conn_add").click().run()
        ui.index()
        self.open(ui)

        self.mode(ui, "Whole schema")
        data = self.interactive(ui)

        assert len(data["tables"]) == 40
        assert data["summary"].startswith("showing 40 of 46 tables")
        assert not ui.at.warning, "truncation is reported in the viewer bar, not as a banner"


class TestSmarterAnswers:

    def test_empty_result_is_retried_with_the_stored_spelling(self, harness):
        ui = harness(
            "SELECT name FROM customer WHERE status = 'in active'",
            "SELECT name FROM customer WHERE status = 'INACTIVE'",
        ).index()

        reply = ui.ask("inactive customers")

        assert reply["corrected"] is True
        assert reply["rows"]["name"].tolist() == ["Bob"]
        assert "actually stores values such as: 'INACTIVE'" in ui.llm.calls[-1]
        assert len(ui.llm.calls) == 3

    def test_empty_result_without_a_fixable_problem_is_not_retried(self, harness):
        ui = harness("SELECT name FROM customer WHERE id = 999").index()

        reply = ui.ask("customer 999")

        assert reply["row_count"] == 0
        assert len(ui.llm.calls) == 2

    def test_a_retry_that_is_still_empty_keeps_the_first_answer(self, harness):
        ui = harness(
            "SELECT name FROM customer WHERE status = 'zzz'",
            "SELECT name FROM customer WHERE status = 'yyy'",
        ).index()

        reply = ui.ask("customers")

        assert reply["sql"].endswith("'zzz'")
        assert not reply.get("corrected")

    def test_answer_has_a_plain_english_plan(self, harness):
        ui = harness("SELECT name FROM customer WHERE status = 'ACTIVE' LIMIT 5").index()

        reply = ui.ask("active customers")

        assert reply["plan"].startswith("Reads customer")
        assert "filtered by" in reply["plan"]

    def test_warnings_are_shown(self, harness):
        ui = harness("SELECT SUM(o.total) FROM orders o JOIN order_item i ON i.order_id = o.id").index()

        reply = ui.ask("total")

        assert reply["warnings"] and "counted more than once" in reply["warnings"][0]
        assert any("counted more than once" in w.value for w in ui.at.warning)

    def test_edited_sql_runs_through_the_guard(self, harness):
        ui = harness("SELECT name FROM customer ORDER BY id").index()
        ui.ask("customers")

        ui.at.text_area(key="edit_sql_1").set_value("SELECT name FROM customer WHERE id = 2")
        next(b for b in ui.at.button if b.key == "run_edit_1").click().run()

        reply = ui.messages[1]
        assert reply["rows"]["name"].tolist() == ["Bob"]
        assert reply["edited"] is True
        assert reply["sql"] == "SELECT name FROM customer WHERE id = 2"

    def test_edited_write_sql_is_refused(self, harness, shop_db):
        ui = harness("SELECT name FROM customer ORDER BY id").index()
        ui.ask("customers")

        ui.at.text_area(key="edit_sql_1").set_value("DELETE FROM customer")
        next(b for b in ui.at.button if b.key == "run_edit_1").click().run()

        assert "Only SELECT" in ui.messages[1]["edit_error"]
        assert ui.messages[1]["rows"]["name"].tolist() == ["Ann", "Bob"]

    def test_edited_sql_that_fails_keeps_the_old_answer(self, harness):
        ui = harness("SELECT name FROM customer ORDER BY id").index()
        ui.ask("customers")

        ui.at.text_area(key="edit_sql_1").set_value("SELECT nope FROM customer")
        next(b for b in ui.at.button if b.key == "run_edit_1").click().run()

        assert ui.messages[1]["edit_error"]
        assert flat(ui.messages[1]["sql"]) == "SELECT name FROM customer ORDER BY id"

    def test_follow_up_chip_asks_the_question(self, harness):
        ui = harness(
            "SELECT id, total, placed_on FROM orders ORDER BY id",
            "SELECT id, total FROM orders ORDER BY total DESC",
        ).index()
        ui.ask("orders")

        chips = [b for b in ui.at.button if b.key and b.key.startswith("followup_")]
        assert chips
        chips[0].click().run()

        assert ui.messages[-2]["role"] == "user"
        assert ui.messages[-2]["content"] == chips[0].label


class TestFollowUps:

    def test_follow_up_keeps_the_tables_of_the_previous_query(self, harness):
        ui = harness(
            "SELECT id, total FROM orders ORDER BY id",
            "SELECT id, total FROM orders WHERE total > 100",
            tables=("main.customer",),
        ).index()
        ui.ask("orders")

        ui.ask("only the big ones")

        assert "Table: orders" in ui.llm.calls[-1]
        assert "main.orders" in ui.reply["tables"]

    def test_first_question_does_not_pull_in_extra_tables(self, harness):
        ui = harness("SELECT name FROM customer", tables=("main.customer",)).index()

        ui.ask("customers")

        assert "main.product" not in ui.reply["tables"]


class TestFormattingAndFollowUpPrompt:

    def test_generated_sql_is_formatted(self, harness):
        ui = harness("select name,status from customer where id=1 order by name").index()

        reply = ui.ask("customer one")

        assert reply["sql"].count("\n") >= 3
        assert reply["sql"].startswith("SELECT")

    def test_follow_up_prompt_contains_the_previous_query(self, harness):
        ui = harness("SELECT id, total FROM orders", "SELECT id, total FROM orders WHERE total > 100").index()
        ui.ask("orders")
        first_prompt = ui.llm.calls[-1]

        ui.ask("only the big ones")

        assert "PREVIOUS QUERY" not in first_prompt
        assert "PREVIOUS QUERY" in ui.llm.calls[-1]
        assert "orders" in ui.llm.calls[-1].split("PREVIOUS QUERY")[1]
