"""The Explorer (navigator, search, table page) through the real app."""

import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

from config.datasource_config import DATASOURCES
from test_app import CHAT, ER, EXPLORER, Harness

pytestmark = pytest.mark.app


@pytest.fixture(scope="session")
def explorer_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("explorer") / "explorer.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE customer(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE, password TEXT);
        CREATE TABLE orders(
            id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customer(id),
            status TEXT, total REAL CHECK (total >= 0));
        CREATE TABLE item(order_id INTEGER REFERENCES orders(id), sku TEXT, qty INTEGER);
        CREATE TABLE secret_notes(id INTEGER PRIMARY KEY, note TEXT);
        CREATE INDEX idx_orders_customer ON orders(customer_id);
        CREATE VIEW open_orders AS SELECT * FROM orders WHERE status = 'OPEN';
        CREATE TRIGGER orders_changed AFTER UPDATE ON orders BEGIN INSERT INTO secret_notes(note) VALUES ('changed'); END;
        INSERT INTO customer VALUES (1,'Ann','ann@x.org','pw1'),(2,'Zoë',NULL,'pw2'),(3,'Bob','bob@x.org',NULL);
        INSERT INTO secret_notes VALUES (1,'hidden');
        """
    )
    statuses = ["OPEN", "SHIPPED", "CANCELLED"]
    con.executemany(
        "INSERT INTO orders VALUES (?,?,?,?)",
        [(i, (i % 3) + 1, statuses[i % 3], float(i * 10)) for i in range(1, 126)],
    )
    con.executemany(
        "INSERT INTO item VALUES (?,?,?)",
        [((i % 125) + 1, f"SKU-{i}", i) for i in range(1, 31)],
    )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def ui(env, monkeypatch, explorer_db):
    DATASOURCES["Shop"]["database"] = explorer_db

    harness = Harness(monkeypatch, "SELECT 1")
    harness.index().goto(EXPLORER)

    return harness


def audit_file():
    return Path(os.environ["SQL_COPILOT_AUDIT_LOG"])


def events(path, name):
    return [
        json.loads(line) for line in path.read_text().splitlines()
        if json.loads(line)["event"] == name
    ]


def selected(ui):
    return ui.at.session_state.explorer_selected


def open_table(ui, key):
    ui.button(key=f"nav_{key}", sidebar=False).click().run()
    assert not ui.at.exception, ui.at.exception
    return ui


def section(ui, name):
    ui.seg("table_section").set_value(name).run()
    assert not ui.at.exception, ui.at.exception
    return ui


def grid(ui):
    return ui.at.dataframe[0].value


def db_counts(path):
    con = sqlite3.connect(path)
    counts = {
        t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("customer", "orders", "item", "secret_notes")
    }
    con.close()
    return counts


class TestNavigator:

    def test_opens_on_the_best_connected_table(self, ui):
        assert selected(ui) in ("main.customer", "main.orders")
        assert ui.at.subheader[0].value == selected(ui).split(".")[1]

    def test_lists_every_table_and_view_of_the_schema(self, ui):
        keys = {b.key for b in ui.at.main.button if b.key.startswith("nav_")}

        assert keys == {
            "nav_main.customer", "nav_main.orders", "nav_main.item",
            "nav_main.secret_notes", "nav_main.open_orders",
        }

    def test_the_open_table_is_highlighted(self, ui):
        open_table(ui, "main.item")

        buttons = {b.key: b for b in ui.at.main.button if b.key.startswith("nav_")}

        assert buttons["nav_main.item"].proto.type == "primary"
        assert buttons["nav_main.orders"].proto.type == "secondary"

    def test_clicking_a_table_opens_it(self, ui):
        open_table(ui, "main.item")

        assert selected(ui) == "main.item"
        assert ui.at.subheader[0].value == "item"

    def test_browsing_the_list_runs_no_queries(self, ui):
        for key in ("main.item", "main.orders", "main.customer"):
            open_table(ui, key)

        assert events(audit_file(), "browse") == []

    def test_header_describes_the_table(self, ui):
        open_table(ui, "main.open_orders")

        caption = " ".join(c.value for c in ui.at.caption)

        assert "view" in caption and "4 columns" in caption


class TestSearch:

    def search(self, ui, text):
        next(t for t in ui.at.text_input if t.key == "explorer_search").set_value(text).run()
        assert not ui.at.exception, ui.at.exception
        return {b.key: b for b in ui.at.main.button if b.key.startswith("nav_")}

    def test_finds_tables_by_name_across_schemas(self, ui):
        assert set(self.search(ui, "ord")) == {"nav_main.orders", "nav_main.open_orders", "nav_main.item"}

    def test_finds_tables_by_column_and_says_why(self, ui):
        results = self.search(ui, "sku")

        assert list(results) == ["nav_main.item"]
        assert "main.item" in results["nav_main.item"].label
        assert "column: sku" in results["nav_main.item"].label

    def test_results_are_qualified_with_their_schema(self, ui):
        assert all(b.label.startswith("main.") for b in self.search(ui, "customer").values())

    def test_the_schema_picker_is_replaced_by_the_results(self, ui):
        assert any(s.key == "explorer_schema" for s in ui.at.selectbox)

        self.search(ui, "item")

        assert not any(s.key == "explorer_schema" for s in ui.at.selectbox)
        assert any("match" in c.value for c in ui.at.caption)

    def test_no_matches_says_so(self, ui):
        assert self.search(ui, "zzzzzz") == {}
        assert any("Nothing matches" in c.value for c in ui.at.caption)

    def test_clearing_the_search_brings_the_list_back(self, ui):
        self.search(ui, "item")
        results = self.search(ui, "")

        assert len(results) == 5
        assert any(s.key == "explorer_schema" for s in ui.at.selectbox)

    def test_picking_a_result_opens_it(self, ui):
        self.search(ui, "sku")

        ui.button(key="nav_main.item", sidebar=False).click().run()

        assert selected(ui) == "main.item"

    def test_several_words_must_all_match(self, ui):
        assert set(self.search(ui, "order status")) == {"nav_main.orders", "nav_main.open_orders"}
        assert set(self.search(ui, "order sku")) == {"nav_main.item"}


class TestProperties:

    def test_columns_with_keys_and_references(self, ui):
        open_table(ui, "main.orders")
        frame = ui.at.dataframe[0].value

        assert frame["Column"].tolist() == ["id", "customer_id", "status", "total"]
        assert frame.set_index("Column").loc["id", "Key"] == "PK"
        assert frame.set_index("Column").loc["customer_id", "Key"] == "FK"
        assert frame.set_index("Column").loc["customer_id", "References"] == "customer.id"
        assert frame.set_index("Column").loc["total", "Null"] == "yes"

    def test_sensitive_columns_are_hidden_by_default(self, ui):
        open_table(ui, "main.customer")

        assert "password" not in ui.at.dataframe[0].value["Column"].tolist()
        assert "email" in ui.at.dataframe[0].value["Column"].tolist()

    def test_follow_a_relationship_forwards(self, ui):
        open_table(ui, "main.orders")

        ui.button(key="ref_out_0", sidebar=False).click().run()

        assert selected(ui) == "main.customer"

    def test_follow_a_relationship_backwards(self, ui):
        open_table(ui, "main.customer")
        labels = [b.label for b in ui.at.main.button if b.key.startswith("ref_in_")]

        assert any(l.startswith("← orders") for l in labels)

        ui.button(key="ref_in_0", sidebar=False).click().run()

        assert selected(ui) == "main.orders"

    def test_empty_relationship_lists_say_so(self, ui):
        open_table(ui, "main.secret_notes")
        text = " ".join(c.value for c in ui.at.caption)

        assert "does not point to other tables" in text
        assert "No other table points to this one" in text

    def test_indexes_are_listed(self, ui):
        open_table(ui, "main.orders")

        indexes = next(d.value for d in ui.at.dataframe if "Method" in d.value.columns)

        assert indexes["Name"].tolist() == ["idx_orders_customer"]
        assert indexes["Columns"].tolist() == ["customer_id"]

    def test_opening_a_table_runs_no_query(self, ui):
        open_table(ui, "main.orders")

        assert events(audit_file(), "browse") == []


class TestData:

    def open_data(self, ui, key="main.orders"):
        open_table(ui, key)
        return section(ui, "Data")

    def rows(self, ui):
        return grid(ui)

    def audit_sql(self, ui):
        return [e["sql"] for e in events(audit_file(), "browse")]

    def test_shows_the_first_page_in_primary_key_order(self, ui):
        self.open_data(ui)

        frame = self.rows(ui)

        assert len(frame) == 50
        assert frame["id"].tolist() == list(range(1, 51))
        assert list(frame.columns) == ["id", "customer_id", "status", "total"]

    def test_the_query_is_plain_and_asks_for_one_extra_row(self, ui):
        self.open_data(ui)

        assert self.audit_sql(ui) == [
            'SELECT "id", "customer_id", "status", "total" FROM "orders" ORDER BY "id" ASC LIMIT 51'
        ]

    def test_paging_forwards_and_back(self, ui):
        self.open_data(ui)
        assert ui.button(key="data_prev", sidebar=False).disabled

        ui.button(key="data_next", sidebar=False).click().run()
        assert self.rows(ui)["id"].tolist()[:2] == [51, 52]

        ui.button(key="data_next", sidebar=False).click().run()
        last = self.rows(ui)
        assert last["id"].tolist() == list(range(101, 126))
        assert ui.button(key="data_next", sidebar=False).disabled, "no extra row, so no next page"

        ui.button(key="data_prev", sidebar=False).click().run()
        assert self.rows(ui)["id"].tolist()[0] == 51

    def test_row_range_is_shown(self, ui):
        self.open_data(ui)
        ui.button(key="data_next", sidebar=False).click().run()

        assert any("Rows 51–100" in c.value for c in ui.at.caption)

    def test_changing_the_page_size_starts_again_from_the_top(self, ui):
        self.open_data(ui)
        ui.button(key="data_next", sidebar=False).click().run()

        next(s for s in ui.at.selectbox if s.key == "data_page_size").set_value(100).run()

        assert len(self.rows(ui)) == 100
        assert self.rows(ui)["id"].tolist()[0] == 1

    def test_sorting(self, ui):
        self.open_data(ui)

        next(s for s in ui.at.selectbox if s.key == "data_sort").set_value("total").run()
        assert self.rows(ui)["total"].tolist()[:3] == [10.0, 20.0, 30.0]

        next(t for t in ui.at.toggle if t.key == "data_desc").set_value(True).run()
        assert self.rows(ui)["total"].tolist()[:3] == [1250.0, 1240.0, 1230.0]
        assert 'ORDER BY "total" DESC' in self.audit_sql(ui)[-1]

    def test_filtering(self, ui):
        self.open_data(ui)

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("status = 'CANCELLED'").run()

        frame = self.rows(ui)
        assert set(frame["status"]) == {"CANCELLED"}
        assert len(frame) == 42
        assert 'WHERE status = \'CANCELLED\'' in self.audit_sql(ui)[-1]

    def test_filter_and_sort_work_together(self, ui):
        self.open_data(ui)
        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("total > 1000").run()
        next(s for s in ui.at.selectbox if s.key == "data_sort").set_value("total").run()
        next(t for t in ui.at.toggle if t.key == "data_desc").set_value(True).run()

        assert self.rows(ui)["total"].tolist() == [1250.0, 1240.0, 1230.0, 1220.0, 1210.0, 1200.0, 1190.0, 1180.0, 1170.0, 1160.0, 1150.0, 1140.0, 1130.0, 1120.0, 1110.0, 1100.0, 1090.0, 1080.0, 1070.0, 1060.0, 1050.0, 1040.0, 1030.0, 1020.0, 1010.0]

    def test_a_filter_with_no_matches(self, ui):
        self.open_data(ui)

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("id < 0").run()

        assert any("No rows match" in i.value for i in ui.at.info)

    @pytest.mark.parametrize(
        "text",
        ["DELETE FROM orders", "1=1; DROP TABLE orders", "SELECT 1", "status ==== 'x'"],
    )
    def test_a_bad_filter_shows_a_message_and_runs_nothing(self, ui, explorer_db, text):
        before = db_counts(explorer_db)
        self.open_data(ui)
        ran = len(self.audit_sql(ui))

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value(text).run()

        assert ui.at.error, "an error message is shown"
        assert len(self.audit_sql(ui)) == ran, "nothing was sent to the database"
        assert db_counts(explorer_db) == before

    def test_the_safety_check_still_applies_to_filters(self, ui):
        self.open_data(ui)

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value(
            "total > (SELECT load_extension('evil'))"
        ).run()

        assert any("not allowed" in e.value for e in ui.at.error)

    def test_excluded_tables_cannot_be_reached_through_a_filter(self, ui):
        ui.widget("multiselect", label="Exclude tables").select("main.secret_notes").run()
        ui.button("Apply privacy settings").click().run()
        ui.goto(EXPLORER)
        self.open_data(ui)

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value(
            "id IN (SELECT id FROM secret_notes)"
        ).run()

        assert any("excluded by the privacy settings" in e.value for e in ui.at.error)

    def test_hidden_columns_never_reach_the_grid(self, ui):
        self.open_data(ui, "main.customer")

        assert "password" not in self.rows(ui).columns
        assert "password" not in self.audit_sql(ui)[-1]

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("password = 'pw1'").run()

        assert any("password" in e.value and "privacy" in e.value for e in ui.at.error)

    def test_nulls_and_unicode_display(self, ui):
        self.open_data(ui, "main.customer")

        frame = self.rows(ui)

        assert "Zoë" in frame["name"].tolist()
        assert frame["email"].isna().sum() == 1

    def test_tables_without_a_primary_key_can_be_paged(self, ui):
        self.open_data(ui, "main.item")
        assert len(self.rows(ui)) == 30
        assert ui.button(key="data_next", sidebar=False).disabled

        next(s for s in ui.at.selectbox if s.key == "data_page_size").set_value(25).run()
        ui.button(key="data_next", sidebar=False).click().run()

        assert len(self.rows(ui)) == 5
        assert "ORDER BY" not in self.audit_sql(ui)[0]

    def test_views_can_be_browsed(self, ui):
        self.open_data(ui, "main.open_orders")

        assert set(self.rows(ui)["status"]) == {"OPEN"}

    def test_count_rows(self, ui):
        self.open_data(ui)
        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("status = 'OPEN'").run()

        ui.button(key="data_count_button", sidebar=False).click().run()

        assert any("41 rows match" in c.value for c in ui.at.caption)

    def test_count_is_forgotten_when_the_filter_changes(self, ui):
        self.open_data(ui)
        ui.button(key="data_count_button", sidebar=False).click().run()
        assert any("125 rows match" in c.value for c in ui.at.caption)

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("id < 10").run()

        assert not any("rows match" in c.value for c in ui.at.caption)

    def test_the_table_size_estimate_is_shown_without_counting(self, ui):
        self.open_data(ui)

        assert not any("rows match" in c.value for c in ui.at.caption)

    def test_the_same_page_is_not_fetched_twice(self, ui):
        self.open_data(ui)
        assert len(self.audit_sql(ui)) == 1

        section(ui, "Properties")
        section(ui, "Data")

        assert len(self.audit_sql(ui)) == 1, "the result was kept"

    def test_refresh_runs_the_query_again(self, ui):
        self.open_data(ui)

        ui.button(key="data_refresh", sidebar=False).click().run()

        assert len(self.audit_sql(ui)) == 2

    def test_column_statistics(self, ui):
        self.open_data(ui)

        stats = ui.at.dataframe[1].value.set_index("Column")

        assert stats.loc["id", "Values"] == 50
        assert stats.loc["status", "Distinct"] == 3
        assert any("Column statistics" in e.label for e in ui.at.main.expander)

    def test_switching_tables_clears_the_filter_sort_and_page(self, ui):
        self.open_data(ui)
        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("id < 100").run()
        next(s for s in ui.at.selectbox if s.key == "data_sort").set_value("total").run()
        ui.button(key="data_next", sidebar=False).click().run()

        open_table(ui, "main.customer")

        state = ui.at.session_state
        assert state["data_filter"] == ""
        assert state["data_sort"] == "(default order)"
        assert "data_page" not in state or state["data_page"] == 0
        assert any("Rows 1–3" in c.value for c in ui.at.caption)

    def test_browsing_is_audited(self, ui):
        self.open_data(ui)

        [event] = events(audit_file(), "browse")

        assert event["table"] == "main.orders"
        assert event["rows"] == 50
        assert event["dialect"] == "sqlite"
        assert event["elapsed_ms"] >= 0


class TestExport:

    def prepare(self, ui, filter_text=None):
        open_table(ui, "main.orders")
        section(ui, "Data")
        if filter_text:
            next(t for t in ui.at.text_input if t.key == "data_filter").set_value(filter_text).run()
        ui.button(key="export_prepare", sidebar=False).click().run()
        assert not ui.at.exception
        return ui.at.session_state["_export"]

    def test_nothing_is_prepared_until_asked(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Data")

        assert "_export" not in ui.at.session_state or ui.at.session_state["_export"] is None
        assert not [d for d in ui.at.get("download_button") if d.proto.label.startswith("⬇")]

    def test_three_formats_are_offered(self, ui):
        self.prepare(ui)

        assert [d.proto.label for d in ui.at.get("download_button")] == ["CSV", "JSON", "Excel"]

    def test_the_whole_table_is_exported_not_just_the_page(self, ui):
        ready = self.prepare(ui)
        files = {label: data for label, _, _, data in ready["files"]}

        assert ready["rows"] == 125 and not ready["truncated"]
        assert len(json.loads(files["JSON"])) == 125
        assert files["CSV"].decode("utf-8-sig").count("\n") == 126

    def test_the_current_filter_applies(self, ui):
        ready = self.prepare(ui, "status = 'CANCELLED'")
        records = json.loads({l: d for l, _, _, d in ready["files"]}["JSON"])

        assert len(records) == 42
        assert {r["status"] for r in records} == {"CANCELLED"}

    def test_hidden_columns_are_not_exported(self, ui):
        open_table(ui, "main.customer")
        section(ui, "Data")
        ui.button(key="export_prepare", sidebar=False).click().run()
        files = {l: d for l, _, _, d in ui.at.session_state["_export"]["files"]}

        assert "password" not in files["CSV"].decode("utf-8-sig").splitlines()[0]
        assert "pw1" not in files["JSON"].decode()

    def test_file_names_follow_the_table(self, ui):
        ready = self.prepare(ui)

        assert [name for _, name, _, _ in ready["files"]] == ["orders.csv", "orders.json", "orders.xlsx"]

    def test_a_bad_filter_offers_no_export(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Data")

        next(t for t in ui.at.text_input if t.key == "data_filter").set_value("DELETE FROM orders").run()

        assert not any(b.key == "export_prepare" for b in ui.at.main.button)

    def test_exports_are_audited(self, ui):
        self.prepare(ui)

        [event] = events(audit_file(), "export")

        assert event["table"] == "main.orders" and event["rows"] == 125

    def test_a_new_table_forgets_the_prepared_files(self, ui):
        self.prepare(ui)

        open_table(ui, "main.customer")

        assert "_export" not in ui.at.session_state


class TestDiagram:

    def data(self, ui):
        html = ui.at.get("iframe")[0].proto.srcdoc
        raw = re.search(r'id="er-data">(.*?)</script>', html, re.S).group(1)
        return json.loads(raw)

    def test_shows_the_table_and_its_neighbours(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Diagram")
        data = self.data(ui)

        assert data["focus"] == "main.orders"
        assert {t["id"] for t in data["tables"]} == {"main.orders", "main.customer", "main.item"}
        assert len(data["edges"]) == 2

    def test_following_two_steps_reaches_further(self, ui):
        open_table(ui, "main.item")
        section(ui, "Diagram")
        assert {t["id"] for t in self.data(ui)["tables"]} == {"main.item", "main.orders"}

        ui.seg("table_depth").set_value(2).run()

        assert {t["id"] for t in self.data(ui)["tables"]} == {"main.item", "main.orders", "main.customer"}

    def test_a_table_without_relationships_is_shown_alone(self, ui):
        open_table(ui, "main.secret_notes")
        section(ui, "Diagram")

        assert [t["id"] for t in self.data(ui)["tables"]] == ["main.secret_notes"]

    def test_the_viewer_can_export(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Diagram")

        exports = self.data(ui)["exports"]

        assert exports["mermaid"].startswith("erDiagram") and exports["dot"].startswith("digraph")

    def test_open_in_the_er_diagram_section(self, ui):
        open_table(ui, "main.item")
        section(ui, "Diagram")

        ui.button(key="open_in_er", sidebar=False).click().run()

        assert ui.seg("nav").value == ER
        assert ui.at.session_state["er_table"] == "main.item"
        assert ui.seg("er_mode").value == "Around a table"
        data = TestDiagram().data(ui)
        assert data["focus"] == "main.item"

    def test_opening_the_diagram_runs_no_query(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Diagram")

        assert events(audit_file(), "browse") == []


class TestSql:

    def choose(self, ui, label):
        next(s for s in ui.at.selectbox if s.key == "sql_template").set_value(label).run()
        assert not ui.at.exception, ui.at.exception

    def sql_shown(self, ui):
        return ui.at.code[0].value

    def open_sql(self, ui, key="main.orders"):
        open_table(ui, key)
        return section(ui, "SQL")

    def test_lists_the_templates(self, ui):
        self.open_sql(ui)
        options = next(s for s in ui.at.selectbox if s.key == "sql_template").options

        assert options[:3] == ["Select the first 100 rows", "Count the rows", "Count rows per column…"]
        assert "Join to customer (through customer_id)" in options
        assert {"Insert a row", "Update a row", "Delete a row", "Create the table (DDL)"} <= set(options)

    def test_shows_runnable_sql_for_this_database(self, ui):
        self.open_sql(ui)

        assert self.sql_shown(ui) == 'SELECT "id", "customer_id", "status", "total" FROM "orders" ORDER BY "id" ASC LIMIT 100'

    def test_running_a_read(self, ui):
        self.open_sql(ui)

        ui.button(key="sql_run", sidebar=False).click().run()

        assert len(ui.at.dataframe[0].value) == 100
        assert any("100 rows" in c.value for c in ui.at.caption)
        assert len(events(audit_file(), "browse")) == 1

    def test_count_template(self, ui):
        self.open_sql(ui)
        self.choose(ui, "Count the rows")

        ui.button(key="sql_run", sidebar=False).click().run()

        assert ui.at.dataframe[0].value["total"].tolist() == [125]

    def test_count_rows_per_column(self, ui):
        self.open_sql(ui)
        self.choose(ui, "Count rows per column…")
        next(s for s in ui.at.selectbox if s.key == "sql_group").set_value("status").run()

        ui.button(key="sql_run", sidebar=False).click().run()

        frame = ui.at.dataframe[0].value.set_index("status")
        assert frame["total"].to_dict() == {"OPEN": 41, "SHIPPED": 42, "CANCELLED": 42}

    def test_join_template_runs(self, ui):
        self.open_sql(ui)
        self.choose(ui, "Join to customer (through customer_id)")

        ui.button(key="sql_run", sidebar=False).click().run()

        assert "customer_name" in ui.at.dataframe[0].value.columns

    @pytest.mark.parametrize("label", ["Insert a row", "Update a row", "Delete a row", "Create the table (DDL)"])
    def test_statements_that_change_things_cannot_be_run(self, ui, explorer_db, label):
        before = db_counts(explorer_db)
        self.open_sql(ui)

        self.choose(ui, label)

        assert not any(b.key == "sql_run" for b in ui.at.main.button), "there is no Run button"
        assert any("never runs statements that change data" in c.value for c in ui.at.caption)
        assert db_counts(explorer_db) == before

    def test_write_templates_show_placeholders(self, ui):
        self.open_sql(ui)
        self.choose(ui, "Delete a row")

        assert self.sql_shown(ui) == 'DELETE FROM "orders"\nWHERE "id" = :id;'

    def test_ddl_is_rebuilt_from_what_was_found(self, ui):
        self.open_sql(ui)
        self.choose(ui, "Create the table (DDL)")

        assert self.sql_shown(ui).startswith('CREATE TABLE "orders" (')
        assert 'FOREIGN KEY ("customer_id") REFERENCES "customer" ("id")' in self.sql_shown(ui)

    def test_views_only_get_reads(self, ui):
        self.open_sql(ui, "main.open_orders")
        options = next(s for s in ui.at.selectbox if s.key == "sql_template").options

        assert "Insert a row" not in options and "Delete a row" not in options

    def test_nothing_runs_until_asked(self, ui):
        self.open_sql(ui)

        assert events(audit_file(), "browse") == []

    def test_running_every_read_template_never_changes_the_database(self, ui, explorer_db):
        before = db_counts(explorer_db)
        for key in ("main.customer", "main.orders", "main.item"):
            self.open_sql(ui, key)
            options = next(s for s in ui.at.selectbox if s.key == "sql_template").options
            for label in options:
                if label.startswith(("Insert", "Update", "Delete", "Create the")):
                    continue
                self.choose(ui, label)
                if any(b.key == "sql_run" for b in ui.at.main.button):
                    ui.button(key="sql_run", sidebar=False).click().run()
                    assert not ui.at.exception

        assert db_counts(explorer_db) == before


class TestPrivacyAndState:

    def test_excluded_tables_vanish_from_the_navigator_and_search(self, ui):
        ui.widget("multiselect", label="Exclude tables").select("main.secret_notes").run()
        ui.button("Apply privacy settings").click().run()
        ui.goto(EXPLORER)

        keys = {b.key for b in ui.at.main.button if b.key.startswith("nav_")}
        assert "nav_main.secret_notes" not in keys

        next(t for t in ui.at.text_input if t.key == "explorer_search").set_value("secret").run()
        assert not [b for b in ui.at.main.button if b.key.startswith("nav_")]

    def test_a_table_that_disappears_is_replaced_in_the_page(self, ui):
        open_table(ui, "main.secret_notes")
        ui.goto(CHAT)
        ui.widget("multiselect", label="Exclude tables").select("main.secret_notes").run()
        ui.button("Apply privacy settings").click().run()

        ui.goto(EXPLORER)

        assert selected(ui) != "main.secret_notes"
        assert not ui.at.exception

    def test_the_selection_is_kept_when_switching_sections(self, ui):
        open_table(ui, "main.item")

        ui.goto(CHAT)
        ui.goto(EXPLORER)

        assert selected(ui) == "main.item"

    def test_before_indexing_the_explorer_asks_to_index(self, env, monkeypatch, explorer_db):
        DATASOURCES["Shop"]["database"] = explorer_db
        harness = Harness(monkeypatch, "SELECT 1")

        harness.goto(EXPLORER)

        assert any("Index the database" in i.value for i in harness.at.info)


class TestEdgeCases:

    def test_an_empty_database_gets_a_clear_message(self, env, monkeypatch, tmp_path):
        path = tmp_path / "empty.db"
        sqlite3.connect(path).close()
        DATASOURCES["Shop"]["database"] = str(path)
        harness = Harness(monkeypatch, "SELECT 1")

        harness.button("Discover & index database").click().run()

        assert not harness.at.exception
        message = " ".join(e.value for e in harness.at.sidebar.error)
        assert "no tables to index" in message
        assert "list index out of range" not in message

    @pytest.mark.parametrize("section", [EXPLORER, ER, CHAT])
    def test_every_section_copes_when_nothing_could_be_indexed(self, env, monkeypatch, tmp_path, section):
        path = tmp_path / "empty.db"
        sqlite3.connect(path).close()
        DATASOURCES["Shop"]["database"] = str(path)
        harness = Harness(monkeypatch, "SELECT 1")
        harness.button("Discover & index database").click().run()

        harness.goto(section)

        assert not harness.at.exception

    def test_excluding_everything_is_refused_and_changes_nothing(self, ui):
        tables_before = {b.key for b in ui.at.main.button if b.key.startswith("nav_")}

        ui.widget("multiselect", label="Exclude schemas").select("main").run()
        ui.button("Apply privacy settings").click().run()

        assert not ui.at.exception
        assert any("Not applied" in e.value and "no tables to index" in e.value for e in ui.at.sidebar.error)
        assert ui.at.session_state.privacy.excluded_schemas == []
        ui.goto(EXPLORER)
        assert {b.key for b in ui.at.main.button if b.key.startswith("nav_")} == tables_before

    def test_a_table_with_a_strange_name_can_be_browsed_and_exported(self, env, monkeypatch, tmp_path):
        path = tmp_path / "odd.db"
        con = sqlite3.connect(path)
        con.executescript(
            'CREATE TABLE "my table"(id INTEGER PRIMARY KEY, "ünï col" TEXT);'
            "INSERT INTO \"my table\" VALUES (1, 'x'), (2, 'y');"
        )
        con.close()
        DATASOURCES["Shop"]["database"] = str(path)
        harness = Harness(monkeypatch, "SELECT 1").index().goto(EXPLORER)

        section(harness, "Data")
        harness.button(key="export_prepare", sidebar=False).click().run()

        assert not harness.at.exception
        assert grid(harness)["ünï col"].tolist() == ["x", "y"]
        names = [name for _, name, _, _ in harness.at.session_state["_export"]["files"]]
        assert names == ["my_table.csv", "my_table.json", "my_table.xlsx"]


class TestColumnProfile:

    def profile(self, ui, table, column):
        open_table(ui, table)
        next(s for s in ui.at.selectbox if s.key == "profile_column").set_value(column).run()
        next(b for b in ui.at.button if b.key == "profile_run").click().run()
        assert not ui.at.exception, ui.at.exception

    def markdown(self, ui):
        return " ".join(m.value for m in ui.at.markdown)

    def metrics(self, ui):
        return {m.label: m.value for m in ui.at.metric}

    def test_nothing_runs_until_asked(self, ui):
        open_table(ui, "main.orders")

        assert events(audit_file(), "profile") == []
        assert not ui.at.metric

    def test_text_column_shows_counts_and_common_values(self, ui):
        self.profile(ui, "main.orders", "status")

        shown = self.metrics(ui)
        assert shown["Rows"] == "125" and shown["Distinct"] == "3"
        assert "`CANCELLED` to `SHIPPED`" in self.markdown(ui)
        assert shown["Nulls"] == "0 (0.0%)"
        common = next(d for d in ui.at.dataframe if "Common value" in d.value.columns)
        assert set(common.value["Common value"]) == {"OPEN", "SHIPPED", "CANCELLED"}

    def test_nulls_are_counted(self, ui):
        self.profile(ui, "main.customer", "email")

        shown = self.metrics(ui)
        assert shown["Nulls"] == "1 (33.3%)" and shown["Distinct"] == "2"

    def test_numeric_column_gets_a_range_and_a_chart(self, ui):
        self.profile(ui, "main.orders", "total")

        shown = self.metrics(ui)
        assert "`10.0` to `1250.0`" in self.markdown(ui)
        assert ui.at.get("arrow_vega_lite_chart") or ui.at.get("vega_lite_chart")

    def test_profiling_is_audited_and_goes_through_the_guard(self, ui):
        self.profile(ui, "main.orders", "status")

        event = events(audit_file(), "profile")[0]
        assert event["table"] == "main.orders" and event["column"] == "status"
        assert "errors" not in event

    def test_hidden_columns_cannot_be_profiled(self, ui):
        open_table(ui, "main.customer")

        options = next(s for s in ui.at.selectbox if s.key == "profile_column").options
        assert "password" not in options


class TestStarredAndRecent:

    def lists(self, ui, choice):
        ui.seg("explorer_list").set_value(choice).run()
        assert not ui.at.exception, ui.at.exception
        return [b.key for b in ui.at.main.button if b.key.startswith("nav_")]

    def test_starring_adds_to_the_starred_list(self, ui):
        open_table(ui, "main.item")
        ui.button(key="fav_toggle", sidebar=False).click().run()

        assert ui.at.session_state.explorer_favorites == ["main.item"]
        assert self.lists(ui, "Starred") == ["nav_main.item"]

    def test_unstarring_removes_it(self, ui):
        open_table(ui, "main.item")
        ui.button(key="fav_toggle", sidebar=False).click().run()
        ui.button(key="fav_toggle", sidebar=False).click().run()

        assert ui.at.session_state.explorer_favorites == []
        assert self.lists(ui, "Starred") == []

    def test_recent_tables_are_most_recent_first(self, ui):
        for key in ("main.item", "main.orders", "main.customer", "main.orders"):
            open_table(ui, key)

        assert self.lists(ui, "Recent")[:3] == ["nav_main.orders", "nav_main.customer", "nav_main.item"]

    def test_recent_list_is_capped(self, ui):
        ui.at.session_state.explorer_recent = [f"main.fake{i}" for i in range(20)]
        open_table(ui, "main.item")

        assert len(ui.at.session_state.explorer_recent) <= 8


class TestRowDetail:

    def select_row(self, ui, number):
        sql = ui.at.session_state["_data_cache"]["sql"]
        from ui.explorer import grid_key

        ui.at.session_state[grid_key(sql, "Shop")] = {"selection": {"rows": [number], "columns": []}}
        ui.at.run()
        assert not ui.at.exception, ui.at.exception

    def test_selecting_a_row_shows_it_in_full(self, ui):
        open_table(ui, "main.customer")
        section(ui, "Data")
        self.select_row(ui, 0)

        detail = next(d for d in ui.at.dataframe if list(d.value.columns) == ["Column", "Value"])
        assert detail.value.set_index("Column").loc["name", "Value"] == "Ann"

    def test_foreign_key_value_links_to_the_parent_row(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Data")
        self.select_row(ui, 0)

        link = next(b for b in ui.at.main.button if b.label.startswith("Open customer where id ="))
        link.click().run()
        assert not ui.at.exception, ui.at.exception

        assert selected(ui) == "main.customer"
        assert ui.at.session_state.table_section == "Data"
        assert ui.at.session_state.data_filter.startswith('"id" = ')
        assert len(grid(ui)) == 1

    def test_referenced_rows_link_to_the_children(self, ui):
        open_table(ui, "main.customer")
        section(ui, "Data")
        self.select_row(ui, 0)

        link = next(b for b in ui.at.main.button if b.label.startswith("Rows of orders where customer_id ="))
        link.click().run()

        assert selected(ui) == "main.orders"
        assert 0 < len(grid(ui)) <= 50
        assert set(grid(ui)["customer_id"]) == {1}

    def test_no_selection_shows_a_hint(self, ui):
        open_table(ui, "main.customer")
        section(ui, "Data")

        assert any("Select a row" in c.value for c in ui.at.caption)


class TestColumnChooser:

    def test_only_chosen_columns_are_read(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Data")
        next(m for m in ui.at.multiselect if m.key == "data_columns").set_value(["status", "total"]).run()

        assert list(grid(ui).columns) == ["status", "total"]
        assert "SELECT" in ui.at.session_state["_data_cache"]["sql"]
        assert '"customer_id"' not in ui.at.session_state["_data_cache"]["sql"]

    def test_choice_is_cleared_for_another_table(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Data")
        next(m for m in ui.at.multiselect if m.key == "data_columns").set_value(["status"]).run()

        open_table(ui, "main.customer")
        section(ui, "Data")

        assert "name" in grid(ui).columns


class TestPlansAndFreeQuery:

    def test_plan_shows_without_running_the_query(self, ui):
        open_table(ui, "main.orders")
        section(ui, "Data")
        before = len(events(audit_file(), "browse"))

        ui.button(key="plan_data", sidebar=False).click().run()

        assert not ui.at.exception, ui.at.exception
        assert any("SCAN" in c.value.upper() or "SEARCH" in c.value.upper() for c in ui.at.code)
        assert len(events(audit_file(), "plan")) == 1
        assert len(events(audit_file(), "browse")) == before

    def run_free(self, ui, sql):
        open_table(ui, "main.orders")
        section(ui, "SQL")
        next(t for t in ui.at.text_area if t.key == "sql_free").set_value(sql).run()
        ui.button(key="free_run", sidebar=False).click().run()
        assert not ui.at.exception, ui.at.exception
        return ui.at.session_state["sql_free_result"]

    def test_a_select_runs(self, ui):
        saved = self.run_free(ui, "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY status")

        assert saved["error"] is None and saved["result"].row_count == 3
        assert events(audit_file(), "query")[0]["rows"] == 3

    def test_writes_are_refused(self, ui, explorer_db):
        before = db_counts(explorer_db)

        saved = self.run_free(ui, "DELETE FROM orders")

        assert "Only SELECT" in saved["error"]
        assert db_counts(explorer_db) == before

    def test_excluded_and_hidden_data_stays_blocked(self, ui):
        saved = self.run_free(ui, "SELECT password FROM customer")

        assert saved["error"] and "password" in saved["error"].lower()

    def test_garbage_is_reported_not_raised(self, ui):
        saved = self.run_free(ui, "this is not sql")

        assert saved["error"]

    def test_generated_sql_can_be_copied_into_the_editor(self, ui):
        open_table(ui, "main.orders")
        section(ui, "SQL")
        ui.button(key="free_template", sidebar=False).click().run()

        assert "SELECT" in ui.at.session_state["sql_free"]

    def test_the_plan_button_appears_for_a_select(self, ui):
        open_table(ui, "main.orders")
        section(ui, "SQL")
        next(t for t in ui.at.text_area if t.key == "sql_free").set_value("SELECT id FROM orders").run()

        assert [b for b in ui.at.main.button if b.key == "plan_free"]


class TestPropertiesDetails:

    def test_view_shows_its_definition(self, ui):
        open_table(ui, "main.open_orders")

        assert any("OPEN" in c.value for c in ui.at.code)
        assert any(e.label == "Definition" for e in ui.at.main.expander)

    def test_constraints_are_listed(self, ui):
        open_table(ui, "main.orders")

        assert any(m.value.startswith("**Constraints**") for m in ui.at.markdown)
        rules = [d.value for d in ui.at.dataframe if "Rule" in d.value.columns]
        assert any("total >= 0" in rule for frame in rules for rule in frame["Rule"])

    def test_table_without_constraints_has_no_section(self, ui):
        open_table(ui, "main.item")

        assert not any(m.value.startswith("**Constraints**") for m in ui.at.markdown)

    def test_plain_table_has_no_definition(self, ui):
        open_table(ui, "main.orders")

        assert not any(e.label == "Definition" for e in ui.at.main.expander)


class TestTriggersHistoryAndStars:

    def test_triggers_are_listed(self, ui):
        open_table(ui, "main.orders")

        assert any(m.value.startswith("**Triggers** (1)") for m in ui.at.markdown)
        assert any(e.label == "orders_changed" for e in ui.at.main.expander)
        assert any("CREATE TRIGGER orders_changed" in c.value for c in ui.at.code)

    def test_a_table_without_triggers_has_no_section(self, ui):
        open_table(ui, "main.item")

        assert not any(m.value.startswith("**Triggers**") for m in ui.at.markdown)

    def test_stars_survive_a_new_session(self, ui, env, monkeypatch, explorer_db):
        open_table(ui, "main.item")
        ui.button(key="fav_toggle", sidebar=False).click().run()

        fresh = Harness(monkeypatch, "SELECT 1")
        fresh.index().goto(EXPLORER)

        assert fresh.at.session_state.explorer_favorites == ["main.item"]

    def test_stars_are_kept_per_datasource(self, ui):
        from services.favorites import load_favorites

        open_table(ui, "main.item")
        ui.button(key="fav_toggle", sidebar=False).click().run()

        assert load_favorites("Shop") == ["main.item"]
        assert load_favorites("Other") == []

    def test_editor_history_lists_recent_queries_and_reloads_them(self, ui):
        open_table(ui, "main.orders")
        section(ui, "SQL")
        editor = lambda: next(t for t in ui.at.text_area if t.key == "sql_free")
        for sql in ("SELECT id FROM orders", "SELECT status FROM orders"):
            editor().set_value(sql).run()
            ui.button(key="free_run", sidebar=False).click().run()

        assert ui.at.session_state.sql_free_history == ["SELECT status FROM orders", "SELECT id FROM orders"]

        ui.button(key="free_history_1", sidebar=False).click().run()
        assert editor().value == "SELECT id FROM orders"

    def test_editor_history_has_no_duplicates_and_ignores_failures_to_run(self, ui):
        open_table(ui, "main.orders")
        section(ui, "SQL")
        for _ in range(2):
            next(t for t in ui.at.text_area if t.key == "sql_free").set_value("SELECT id FROM orders").run()
            ui.button(key="free_run", sidebar=False).click().run()

        assert ui.at.session_state.sql_free_history == ["SELECT id FROM orders"]
