import pytest

from conftest import make_metadata, make_table
from database.schema_metadata import ForeignKeyMetadata, infer_relationships
from services.er_diagram import (
    build_dot,
    build_mermaid,
    expand_around,
    most_connected,
    schema_tables,
    table_index,
)


@pytest.fixture
def index():
    """a <- b <- c (explicit), d isolated, x.e -> a (other schema, explicit)."""

    a = make_table("a", ("id", "name"))
    b = make_table(
        "b", ("id", "a_id", "note"),
        foreign_keys=[ForeignKeyMetadata("a_id", "public", "a", "id")],
    )
    c = make_table(
        "c", ("id", "b_id"),
        foreign_keys=[ForeignKeyMetadata("b_id", "public", "b", "id")],
    )
    d = make_table("d", ("id",))
    e = make_table(
        "e", ("id", "a_ref"), schema="x",
        foreign_keys=[ForeignKeyMetadata("a_ref", "public", "a", "id")],
    )
    return table_index(make_metadata(("public", [a, b, c, d]), ("x", [e])))


def test_table_index_keys(index):
    assert set(index) == {"public.a", "public.b", "public.c", "public.d", "x.e"}


def test_expand_around_respects_hops_and_order(index):
    assert expand_around(index, "public.a", 0) == ["public.a"]
    assert expand_around(index, "public.a", 1) == ["public.a", "public.b", "x.e"]
    assert expand_around(index, "public.a", 2) == [
        "public.a", "public.b", "x.e", "public.c",
    ]


def test_expand_around_isolated_table(index):
    assert expand_around(index, "public.d", 3) == ["public.d"]


def test_inferred_relationships_can_be_excluded():
    parent = make_table("student")
    child = make_table("trip", ("id", "student_id"))
    metadata = make_metadata(("public", [parent, child]))
    infer_relationships(metadata)
    index = table_index(metadata)

    assert expand_around(index, "public.student", 1, include_inferred=True) == [
        "public.student", "public.trip",
    ]
    assert expand_around(index, "public.student", 1, include_inferred=False) == [
        "public.student",
    ]


def test_schema_tables_orders_by_connectivity_and_hides_isolated(index):
    assert schema_tables(index, "public") == ["public.a", "public.b", "public.c"]
    assert "public.d" in schema_tables(index, "public", hide_isolated=False)
    assert schema_tables(index, "x") == ["x.e"]
    assert schema_tables(index, "missing") == []


def test_most_connected(index):
    assert most_connected(index) == "public.a"
    assert most_connected({}) is None


class TestDot:

    def test_structure(self, index):
        dot = build_dot(index, ["public.a", "public.b"])

        assert dot.startswith("digraph ER {") and dot.rstrip().endswith("}")
        assert dot.count("[label=<<TABLE") == 2
        assert dot.count(" -> ") == 1

    def test_keys_only_mode_shows_pk_and_fk_columns(self, index):
        dot = build_dot(index, ["public.a", "public.b"], "Keys only")

        assert "PK id" in dot
        assert "FK a_id" in dot
        assert "note" not in dot

    def test_all_columns_mode(self, index):
        assert "note" in build_dot(index, ["public.a", "public.b"], "All columns")

    def test_names_only_mode_has_no_column_rows(self, index):
        dot = build_dot(index, ["public.a", "public.b"], "Names only")

        assert "bigint" not in dot
        assert dot.count(" -> ") == 1

    def test_edges_only_between_selected_tables(self, index):
        assert " -> " not in build_dot(index, ["public.a", "public.c"])

    def test_schemas_are_prefixed_when_mixed(self, index):
        dot = build_dot(index, ["public.a", "x.e"])

        assert "<B>x.e</B>" in dot
        assert "<B>public.a</B>" in dot

    def test_single_schema_diagrams_omit_the_prefix(self, index):
        dot = build_dot(index, ["x.e"])

        assert "<B>e</B>" in dot
        assert "<B>x.e</B>" not in dot

    def test_inferred_edges_are_dashed(self):
        metadata = make_metadata(("public", [make_table("student"), make_table("trip", ("id", "student_id"))]))
        infer_relationships(metadata)
        index = table_index(metadata)
        keys = ["public.student", "public.trip"]

        assert "style=dashed" in build_dot(index, keys, include_inferred=True)
        assert " -> " not in build_dot(index, keys, include_inferred=False)

    def test_special_characters_are_escaped(self):
        weird = make_table("a<b>&c", ("id", "x<y"))
        index = table_index(make_metadata(("public", [weird])))

        dot = build_dot(index, ["public.a<b>&c"], "All columns")

        assert "a&lt;b&gt;&amp;c" in dot
        assert "x&lt;y" in dot

    def test_views_are_labelled(self):
        index = table_index(make_metadata(("public", [make_table("v", kind="view")])))

        assert "(view)" in build_dot(index, ["public.v"])

    def test_long_tables_keep_key_columns_when_truncated(self):
        columns = tuple(f"c{i}" for i in range(60)) + ("parent_id",)
        parent = make_table("parent")
        wide = make_table(
            "wide", ("id",) + columns,
            foreign_keys=[ForeignKeyMetadata("parent_id", "public", "parent", "id")],
        )
        index = table_index(make_metadata(("public", [parent, wide])))

        dot = build_dot(index, ["public.parent", "public.wide"], "All columns")

        assert "FK parent_id" in dot
        assert dot.count(" -> ") == 1


class TestMermaid:

    def test_structure_and_cardinality(self, index):
        text = build_mermaid(index, ["public.a", "public.b"])

        assert text.startswith("erDiagram")
        assert "a ||--o{ b : \"a_id\"" in text
        assert "bigint id PK" in text
        assert "bigint a_id FK" in text

    def test_inferred_label(self):
        metadata = make_metadata(("public", [make_table("student"), make_table("trip", ("id", "student_id"))]))
        infer_relationships(metadata)

        text = build_mermaid(table_index(metadata), ["public.student", "public.trip"])

        assert '"student_id (inferred)"' in text

    def test_name_clash_uses_schema_prefix(self):
        index = table_index(
            make_metadata(("a", [make_table("t", schema="a")]), ("b", [make_table("t", schema="b")]))
        )

        text = build_mermaid(index, ["a.t", "b.t"])

        assert "  t {" in text
        assert "  b_t {" in text

    def test_types_and_names_are_sanitised(self):
        table = make_table("t", ("my col",))
        table.columns[0].data_type = "character varying(255)"
        index = table_index(make_metadata(("public", [table])))

        text = build_mermaid(index, ["public.t"], "All columns")

        assert "character_varying_255 my_col" in text
