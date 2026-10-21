import pytest

from conftest import make_metadata, make_table
from database.schema_metadata import ColumnMetadata, ForeignKeyMetadata, IndexMetadata
from database.sql_guard import validate_sql
from services.er_diagram import table_index
from services.sql_templates import (
    REVIEW_ONLY,
    create_table_ddl,
    generate_templates,
    group_template,
    index_ddl,
)


@pytest.fixture
def world():
    customer = make_table("customer", ("id", "name"))
    customer.columns[1].data_type = "text"
    orders = make_table(
        "orders", ("id", "customer_id", "total"),
        foreign_keys=[ForeignKeyMetadata("customer_id", "public", "customer", "id")],
    )
    orders.columns[2].nullable = "NO"
    orders.columns[2].default = "0"
    orders.indexes.append(
        IndexMetadata("orders_customer_idx", ["customer_id"], False, "btree",
                      "CREATE INDEX orders_customer_idx ON public.orders USING btree (customer_id)")
    )
    item = make_table("item", ("id", "order_id", "customer_id"))
    item.foreign_keys.append(ForeignKeyMetadata("order_id", "public", "orders", "id", inferred=True))
    item.foreign_keys.append(ForeignKeyMetadata("customer_id", "public", "customer", "id", inferred=True))
    index = table_index(make_metadata(("public", [customer, orders, item])))
    return index


def by_label(templates, start):
    return next(t for t in templates if t.label.startswith(start))


class TestSelection:

    def test_runnable_reads_come_first(self, world):
        templates = generate_templates(world["public.orders"], world, "postgresql")
        flags = [t.runnable for t in templates]

        assert flags == sorted(flags, reverse=True)
        assert templates[0].label.startswith("Select the first")
        assert templates[1].label == "Count the rows"

    def test_a_join_is_offered_for_each_readable_parent(self, world):
        labels = [t.label for t in generate_templates(world["public.orders"], world, "postgresql")]

        assert "Join to customer (through customer_id)" in labels

    def test_rows_pointing_here_are_offered_for_referenced_tables(self, world):
        labels = [t.label for t in generate_templates(world["public.customer"], world, "postgresql")]

        assert "Rows of orders that point here" in labels

    def test_no_join_templates_without_a_readable_column(self):
        parent = make_table("parent", ("id", "qty"))
        child = make_table(
            "child", ("id", "parent_id"),
            foreign_keys=[ForeignKeyMetadata("parent_id", "public", "parent", "id")],
        )
        index = table_index(make_metadata(("public", [parent, child])))

        labels = [t.label for t in generate_templates(child, index, "postgresql")]

        assert not any(label.startswith("Join to") for label in labels)
        assert not any("point here" in t.label for t in generate_templates(parent, index, "postgresql"))

    def test_inferred_relationships_are_labelled(self, world):
        templates = generate_templates(world["public.customer"], world, "postgresql")
        child = by_label(templates, "Rows of item")

        assert "inferred" in child.note

    def test_views_get_reads_only(self):
        view = make_table("v", ("id", "name"), kind="view")
        index = table_index(make_metadata(("public", [view])))
        labels = [t.label for t in generate_templates(view, index, "postgresql")]

        assert not any(l in labels for l in ("Insert a row", "Update a row", "Delete a row"))


class TestSafety:

    @pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mssql", "sqlite", "oracle"])
    def test_every_runnable_template_passes_the_safety_check(self, world, dialect):
        for table in world.values():
            for template in generate_templates(table, world, dialect):
                if template.runnable:
                    assert validate_sql(template.sql, None, dialect).allowed, (dialect, template.sql)

    def test_nothing_that_changes_data_or_structure_is_runnable(self, world):
        for table in world.values():
            for template in generate_templates(table, world, "postgresql"):
                head = template.sql.lstrip().upper()
                if head.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE")):
                    assert not template.runnable, template.label
                    assert REVIEW_ONLY in template.note or "not available" in template.sql

    def test_review_only_templates_would_be_rejected_by_the_guard(self, world):
        for template in generate_templates(world["public.orders"], world, "postgresql"):
            if not template.runnable and not template.sql.startswith("--"):
                assert not validate_sql(template.sql.split(";")[0], None, "postgresql").allowed


class TestWriteTemplates:

    def test_insert(self, world):
        sql = by_label(generate_templates(world["public.orders"], world, "postgresql"), "Insert").sql

        assert sql.startswith('INSERT INTO "public"."orders"')
        assert ":id, :customer_id, :total" in sql

    def test_update_keeps_a_where_clause_on_the_primary_key(self, world):
        sql = by_label(generate_templates(world["public.orders"], world, "postgresql"), "Update").sql

        assert 'WHERE "id" = :id' in sql
        assert '"id" = :id,' not in sql, "the key is not one of the assignments"

    def test_delete_keeps_a_where_clause(self, world):
        sql = by_label(generate_templates(world["public.orders"], world, "postgresql"), "Delete").sql

        assert sql == 'DELETE FROM "public"."orders"\nWHERE "id" = :id;'

    def test_tables_without_a_key_use_their_first_column(self):
        table = make_table("log", ("ts", "msg"), primary_key=None)
        index = table_index(make_metadata(("public", [table])))

        sql = by_label(generate_templates(table, index, "postgresql"), "Delete").sql

        assert 'WHERE "ts" = :ts' in sql

    @pytest.mark.parametrize(
        "dialect,quoted",
        [("mysql", "`orders`"), ("mssql", "[orders]"), ("sqlite", '"orders"')],
    )
    def test_quoting_follows_the_database(self, world, dialect, quoted):
        assert quoted in by_label(generate_templates(world["public.orders"], world, dialect), "Delete").sql


class TestDdl:

    def test_create_table(self, world):
        ddl = create_table_ddl(world["public.orders"], world, "postgresql").sql

        assert ddl.startswith('CREATE TABLE "public"."orders" (')
        assert '"total" bigint NOT NULL DEFAULT 0' in ddl
        assert 'PRIMARY KEY ("id")' in ddl
        assert 'FOREIGN KEY ("customer_id") REFERENCES "public"."customer" ("id")' in ddl
        assert ddl.rstrip().endswith(");")

    def test_guessed_relationships_are_not_written_as_constraints(self, world):
        ddl = create_table_ddl(world["public.item"], world, "postgresql").sql

        assert "FOREIGN KEY" not in ddl

    def test_ddl_says_it_is_reconstructed(self, world):
        note = create_table_ddl(world["public.orders"], world, "postgresql").note

        assert "Rebuilt from the discovered" in note

    def test_views_have_no_definition_yet(self):
        view = make_table("v", ("id",), kind="view")
        template = create_table_ddl(view, {}, "postgresql")

        assert "not available" in template.sql and not template.runnable

    def test_index_ddl_uses_the_real_definition_when_known(self, world):
        template = index_ddl(world["public.orders"], "postgresql")

        assert template.sql == "CREATE INDEX orders_customer_idx ON public.orders USING btree (customer_id);"

    def test_index_ddl_is_rebuilt_when_only_columns_are_known(self):
        table = make_table("t", ("a", "b"))
        table.indexes.append(IndexMetadata("t_ab", ["a", "b"], True, ""))

        assert index_ddl(table, "postgresql").sql == 'CREATE UNIQUE INDEX "t_ab" ON "public"."t" ("a", "b");'

    def test_no_indexes_no_template(self):
        assert index_ddl(make_table("t"), "postgresql") is None


def test_group_by_column_template(world):
    customer = world["public.customer"]
    template = group_template(customer, customer.columns[1], "postgresql")

    assert template.runnable
    assert template.sql == (
        'SELECT "name", COUNT(*) AS total FROM "public"."customer" '
        'GROUP BY "name" ORDER BY "total" DESC LIMIT 50'
    )
    assert validate_sql(template.sql, None, "postgresql").allowed
