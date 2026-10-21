import json
import re

import pytest

from conftest import make_metadata, make_table
from database.schema_metadata import ForeignKeyMetadata, infer_relationships
from services.er_diagram import table_index
from services.er_interactive import (
    MAX_HEIGHT,
    MIN_HEIGHT,
    graph_data,
    short_type,
    viewer_height,
    viewer_html,
)


@pytest.fixture
def index():
    customer = make_table("customer", ("id", "name", "note"))
    orders = make_table(
        "orders", ("id", "customer_id", "total"),
        foreign_keys=[ForeignKeyMetadata("customer_id", "public", "customer", "id")],
    )
    item = make_table("order_item", ("id", "order_id", "sku"))
    item.foreign_keys.append(ForeignKeyMetadata("order_id", "public", "orders", "id", inferred=True))
    other = make_table("audit", ("id",), schema="ops")
    return table_index(make_metadata(("public", [customer, orders, item]), ("ops", [other])))


KEYS = ["public.customer", "public.orders", "public.order_item"]


class TestGraphData:

    def test_tables_and_edges(self, index):
        data = graph_data(index, KEYS)

        assert [t["id"] for t in data["tables"]] == KEYS
        assert data["edges"] == [
            {"child": "public.orders", "childColumn": "customer_id",
             "parent": "public.customer", "parentColumn": "id", "inferred": False},
            {"child": "public.order_item", "childColumn": "order_id",
             "parent": "public.orders", "parentColumn": "id", "inferred": True},
        ]

    def test_key_flags(self, index):
        orders = next(t for t in graph_data(index, KEYS)["tables"] if t["name"] == "orders")

        flags = {c["name"]: (c["pk"], c["fk"]) for c in orders["columns"]}

        assert flags == {"id": (True, False), "customer_id": (False, True)}

    def test_keys_only_hides_plain_columns(self, index):
        customer = next(t for t in graph_data(index, KEYS, "Keys only")["tables"] if t["name"] == "customer")

        assert [c["name"] for c in customer["columns"]] == ["id"]

    def test_all_columns_and_names_only(self, index):
        everything = graph_data(index, KEYS, "All columns")
        nothing = graph_data(index, KEYS, "Names only")

        assert [c["name"] for c in everything["tables"][0]["columns"]] == ["id", "name", "note"]
        assert all(t["columns"] == [] for t in nothing["tables"])
        assert len(nothing["edges"]) == 2

    def test_referenced_non_key_column_is_still_shown(self):
        parent = make_table("parent", ("id", "code"), primary_key="id")
        child = make_table(
            "child", ("id", "parent_code"),
            foreign_keys=[ForeignKeyMetadata("parent_code", "public", "parent", "code")],
        )
        index = table_index(make_metadata(("public", [parent, child])))

        parent_columns = graph_data(index, ["public.parent", "public.child"])["tables"][0]["columns"]

        assert "code" in [c["name"] for c in parent_columns]

    def test_edges_only_between_selected_tables(self, index):
        data = graph_data(index, ["public.customer", "public.order_item"])

        assert data["edges"] == []

    def test_inferred_can_be_excluded(self, index):
        data = graph_data(index, KEYS, include_inferred=False)

        assert [e["inferred"] for e in data["edges"]] == [False]

    def test_focus(self, index):
        assert graph_data(index, KEYS)["focus"] == "public.customer"
        assert graph_data(index, KEYS, focus="public.orders")["focus"] == "public.orders"
        assert graph_data(index, KEYS, focus="nope")["focus"] == "public.customer"
        assert graph_data(index, [])["focus"] is None

    def test_schema_prefix_only_when_mixed(self, index):
        assert graph_data(index, KEYS)["showSchema"] is False
        assert graph_data(index, KEYS + ["ops.audit"])["showSchema"] is True

    def test_types_are_shortened(self):
        assert short_type("character varying(255)") == "varchar(255)"
        assert short_type("timestamp without time zone") == "timestamp"
        assert short_type("timestamp with time zone") == "timestamptz"
        assert short_type("bigint") == "bigint"
        assert short_type(None) == ""

    def test_data_is_json_serialisable(self, index):
        json.dumps(graph_data(index, KEYS + ["ops.audit"], "All columns"))

    def test_theme_and_summary_pass_through(self, index):
        data = graph_data(index, KEYS, theme="dark", summary="3 tables")

        assert data["theme"] == "dark"
        assert data["summary"] == "3 tables"

    def test_exports_pass_through(self, index):
        data = graph_data(index, KEYS, exports={"mermaid": "erDiagram", "dot": "digraph {}"})

        assert data["exports"] == {"mermaid": "erDiagram", "dot": "digraph {}"}
        assert graph_data(index, KEYS)["exports"] == {}

    def test_unknown_theme_is_dropped(self, index):
        assert graph_data(index, KEYS, theme="sepia")["theme"] is None
        assert graph_data(index, KEYS)["theme"] is None
        assert graph_data(index, KEYS)["summary"] is None

    def test_views_are_marked(self):
        view = make_table("v", ("id",), kind="view")
        data = graph_data(table_index(make_metadata(("public", [view]))), ["public.v"])

        assert data["tables"][0]["kind"] == "view"


class TestViewerHeight:

    def test_small_diagrams_get_a_compact_frame(self):
        assert viewer_height(1) == MIN_HEIGHT
        assert viewer_height(3) == 390

    def test_height_grows_with_tables(self):
        assert viewer_height(3) < viewer_height(5) < viewer_height(7)

    def test_height_is_capped(self):
        assert viewer_height(8) == MAX_HEIGHT
        assert viewer_height(40) == MAX_HEIGHT

    def test_empty(self):
        assert viewer_height(0) == MIN_HEIGHT


def embedded(html):
    return re.search(r'<script type="application/json" id="er-data">(.*?)</script>', html, re.S).group(1)


class TestViewerHtml:

    def test_assets_are_inlined(self, index):
        html = viewer_html(graph_data(index, KEYS))

        for placeholder in ("__CSS__", "__JS__", "__DATA__"):
            assert placeholder not in html
        assert "function buildModel" in html
        assert "--accent" in html
        assert json.loads(embedded(html))["focus"] == "public.customer"

    def test_data_round_trips(self, index):
        data = graph_data(index, KEYS, "All columns")

        assert json.loads(embedded(viewer_html(data))) == data

    def test_markup_in_names_cannot_break_out_of_the_data_block(self):
        evil = '</script><script>window.__pwned=1</script><img src=x onerror="window.__pwned=1">'
        table = make_table(evil, (evil,), schema=evil)
        table.columns[0].data_type = evil
        index = table_index(make_metadata((evil, [table])))

        html = viewer_html(graph_data(index, [f"{evil}.{evil}"], "All columns"))
        block = embedded(html)

        assert "</script" not in block
        assert "<img" not in block and "<script" not in block
        assert html.count("</script>") == 2
        assert html.count("<script") == 2
        assert json.loads(block)["tables"][0]["name"] == evil

    def test_placeholders_inside_data_are_not_expanded(self):
        table = make_table("__JS__", ("__CSS__",), schema="__DATA__")
        index = table_index(make_metadata(("__DATA__", [table])))

        html = viewer_html(graph_data(index, ["__DATA__.__JS__"], "All columns"))
        data = json.loads(embedded(html))

        assert data["tables"][0]["name"] == "__JS__"
        assert data["tables"][0]["columns"][0]["name"] == "__CSS__"

    def test_line_separators_are_escaped(self):
        table = make_table("a b", ("id",))
        index = table_index(make_metadata(("public", [table])))

        block = embedded(viewer_html(graph_data(index, ["public.a b"])))

        assert " " not in block
