import pandas as pd
import pytest

from conftest import make_metadata, make_table
from database.database_inspector import inspect_database
from database.schema_metadata import (
    ForeignKeyMetadata,
    IndexMetadata,
    infer_shared_key_relationships,
)
from database.sql_guard import validate_sql
from services.followups import suggest_followups
from services.query_checks import find_stored_values, review
from services.sql_explain import describe_sql


@pytest.fixture
def shop_metadata(shop_config):
    return inspect_database("Shop", shop_config)


def with_values(metadata, table, column, values, kind="common"):
    for schema in metadata.schemas:
        for item in schema.tables:
            if item.name == table:
                for col in item.columns:
                    if col.name == column:
                        col.values = values
                        col.values_kind = kind


class TestDescribe:

    def test_filters_groups_sorting_and_limit(self):
        text = describe_sql(
            "SELECT status, COUNT(*) FROM customer c JOIN orders o ON o.customer_id = c.id "
            "WHERE status = 'ACTIVE' AND total > 10 GROUP BY status ORDER BY 2 DESC LIMIT 5",
            "sqlite",
        )
        assert "Reads customer joined with orders" in text
        assert "number of rows" in text
        assert "grouped by status" in text
        assert "status = 'ACTIVE'" in text and "total > 10" in text
        assert "highest first" in text
        assert "first 5 rows" in text

    def test_ctes_are_not_listed_as_tables(self):
        text = describe_sql("WITH x AS (SELECT * FROM orders) SELECT * FROM x", "sqlite")
        assert text == "Reads orders"

    def test_unparseable_sql_gives_nothing(self):
        assert describe_sql("not sql at all ((", "sqlite") == ""


class TestFollowups:

    def test_suggestions_fit_the_result(self):
        rows = pd.DataFrame(
            {
                "id": range(15),
                "status": ["A", "B", "C"] * 5,
                "total": range(15),
                "placed_on": ["2026-01-01"] * 15,
            }
        )
        found = suggest_followups(rows, "SELECT * FROM t")
        assert found == [
            "Show only the top 10",
            "Break this down by status",
            "Show this per month",
        ]

    def test_nothing_for_empty_results(self):
        assert suggest_followups(pd.DataFrame({"a": []})) == []

    def test_does_not_suggest_sorting_by_what_is_sorted(self):
        rows = pd.DataFrame({"total": [3, 2, 1]})
        assert suggest_followups(rows, "select total from t order by total desc") == []


class TestReview:

    def test_separator_mismatch_is_found_for_empty_results(self, shop_metadata):
        with_values(shop_metadata, "customer", "status", ["ACTIVE.NOW", "INACTIVE"])
        issues = review(
            "SELECT * FROM customer WHERE status = 'active now'",
            pd.DataFrame(),
            "sqlite",
            shop_metadata,
        )
        assert issues and issues[0].fixable
        assert "ACTIVE.NOW" in issues[0].text

    def test_text_filters_without_known_values_are_flagged_when_empty(self, shop_metadata):
        issues = review(
            "SELECT * FROM customer WHERE name = 'ann smith'",
            pd.DataFrame(),
            "sqlite",
            shop_metadata,
        )
        assert issues and issues[0].kind == "unknown_value"

    def test_no_value_issues_when_rows_came_back(self, shop_metadata):
        issues = review(
            "SELECT * FROM customer WHERE name = 'Ann'",
            pd.DataFrame({"id": [1]}),
            "sqlite",
            shop_metadata,
        )
        assert issues == []

    def test_fan_out_when_summing_a_parent_column_across_its_children(self, shop_metadata):
        issues = review(
            "SELECT SUM(o.total) FROM orders o JOIN order_item i ON i.order_id = o.id",
            pd.DataFrame({"s": [1]}),
            "sqlite",
            shop_metadata,
        )
        assert [issue.kind for issue in issues] == ["fan_out"]

    def test_summing_the_child_column_is_fine(self, shop_metadata):
        issues = review(
            "SELECT SUM(i.qty) FROM orders o JOIN order_item i ON i.order_id = o.id",
            pd.DataFrame({"s": [1]}),
            "sqlite",
            shop_metadata,
        )
        assert issues == []

    def test_cartesian_product(self, shop_metadata):
        issues = review(
            "SELECT * FROM customer, product",
            pd.DataFrame({"a": [1]}),
            "sqlite",
            shop_metadata,
        )
        assert any(issue.kind == "cartesian" for issue in issues)

    def test_all_null_column(self, shop_metadata):
        issues = review(
            "SELECT 1",
            pd.DataFrame({"a": [None, None]}),
            "sqlite",
            shop_metadata,
        )
        assert issues[0].kind == "all_null"


class TestStoredValues:

    def allowed(self, sql):
        return validate_sql(sql, None, "sqlite").allowed

    def test_finds_how_a_value_is_really_stored(self, shop_metadata, shop_config):
        issues = review(
            "SELECT * FROM customer WHERE status = 'in active'",
            pd.DataFrame(),
            "sqlite",
            shop_metadata,
        )
        hints = find_stored_values(issues, "Shop", shop_config, "sqlite", self.allowed)
        assert hints and "INACTIVE" in hints[0]

    def test_queries_must_pass_the_guard(self, shop_metadata, shop_config):
        issues = review(
            "SELECT * FROM customer WHERE status = 'in active'",
            pd.DataFrame(),
            "sqlite",
            shop_metadata,
        )
        assert find_stored_values(issues, "Shop", shop_config, "sqlite", lambda sql: False) == []


class TestSharedKeys:

    def build(self):
        principal = make_table("principal", ("sub", "first_name"), primary_key="sub")
        principal.indexes = [IndexMetadata("principal_sub_key", ["sub"], True, "btree")]
        device = make_table("device_token", ("id", "principal_sub", "token"))
        other = make_table("note", ("id", "sub"))
        other.columns[1].data_type = "text"
        metadata = make_metadata(("public", [principal, device, other]))
        infer_shared_key_relationships(metadata)
        return device, other

    def test_prefixed_identifier_links_to_the_owner(self):
        device, _ = self.build()
        assert [(fk.column, fk.foreign_table, fk.foreign_column, fk.inferred) for fk in device.foreign_keys] == [
            ("principal_sub", "principal", "sub", True)
        ]

    def test_different_types_are_not_linked(self):
        _, other = self.build()
        assert other.foreign_keys == []

    def test_generic_names_are_never_used(self):
        a = make_table("a", ("id", "status"), primary_key="status")
        b = make_table("b", ("id", "status"))
        metadata = make_metadata(("public", [a, b]))
        infer_shared_key_relationships(metadata)
        assert b.foreign_keys == []
