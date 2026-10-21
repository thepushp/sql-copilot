import sqlite3

import pytest

from config.datasource_config import DATASOURCES
from database import query_executor
from database.connection_manager import get_engine
from database.database_inspector import inspect_database
from database.generic_inspector import inspect_generic
from database.query_executor import execute_query, make_unique_column_names


class TestConnectionManager:

    def test_missing_fields_are_reported(self):
        with pytest.raises(ValueError, match="database, host, username"):
            get_engine("x", {"driver": "postgresql"})

    def test_sqlite_only_needs_a_database(self):
        with pytest.raises(ValueError, match="database"):
            get_engine("x", {"driver": "sqlite"})

    def test_engines_are_cached_per_credentials(self, shop_config):
        assert get_engine("shop", shop_config) is get_engine("shop", dict(shop_config))
        assert get_engine("shop", shop_config) is not get_engine("shop-2", shop_config)

    def test_different_password_gives_different_engine(self):
        base = {"driver": "mysql", "host": "h", "port": 3306, "database": "d", "username": "u"}

        first = get_engine("m", {**base, "password": "one"})
        second = get_engine("m", {**base, "password": "two"})

        assert first is not second

    def test_datasource_name_lookup(self, monkeypatch, shop_config):
        monkeypatch.setitem(DATASOURCES, "ShopEnv", shop_config)

        assert get_engine("ShopEnv").dialect.name == "sqlite"


class TestExecutor:

    def test_select_returns_dataframe(self, shop_config):
        result = execute_query("shop", "SELECT name FROM customer ORDER BY id", shop_config)

        assert result.error is None
        assert result.row_count == 2
        assert list(result.rows["name"]) == ["Ann", "Bob"]
        assert result.execution_ms >= 0

    def test_join_and_aggregation(self, shop_config):
        sql = (
            "SELECT c.name, SUM(o.total) AS spent FROM customer c "
            "JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY spent DESC"
        )

        rows = execute_query("shop", sql, shop_config).rows

        assert rows.to_dict("records") == [
            {"name": "Bob", "spent": 300.0},
            {"name": "Ann", "spent": 170.0},
        ]

    def test_errors_are_returned_not_raised(self, shop_config):
        result = execute_query("shop", "SELECT nope FROM customer", shop_config)

        assert result.rows is None
        assert result.row_count == 0
        assert "nope" in result.error

    def test_database_itself_refuses_writes(self, shop_config, shop_db):
        for sql in (
            "INSERT INTO customer VALUES (9, 'Eve', 'ACTIVE')",
            "DELETE FROM customer",
            "DROP TABLE customer",
        ):
            assert execute_query("shop", sql, shop_config).error

        con = sqlite3.connect(shop_db)
        assert con.execute("SELECT count(*) FROM customer").fetchone()[0] == 2
        con.close()

    def test_row_cap(self, shop_config, monkeypatch):
        monkeypatch.setattr(query_executor, "MAX_ROWS", 2)

        result = execute_query("shop", "SELECT * FROM order_item", shop_config)

        assert result.row_count == 2

    def test_the_caller_can_ask_for_a_different_number_of_rows(self, shop_config):
        assert execute_query("shop", "SELECT * FROM order_item", shop_config, max_rows=1).row_count == 1
        assert execute_query("shop", "SELECT * FROM order_item", shop_config, max_rows=1000).row_count == 3

    def test_the_default_cap_follows_the_module_setting(self, shop_config, monkeypatch):
        monkeypatch.setattr(query_executor, "MAX_ROWS", 1)

        assert execute_query("shop", "SELECT * FROM order_item", shop_config).row_count == 1

    def test_duplicate_column_names_are_made_unique(self, shop_config):
        result = execute_query("shop", "SELECT 1 AS a, 2 AS a, 3 AS a", shop_config)

        assert list(result.rows.columns) == ["a", "a_1", "a_2"]

    def test_make_unique_column_names(self):
        assert make_unique_column_names(["id", "x", "id", "id"]) == ["id", "x", "id_1", "id_2"]
        assert make_unique_column_names([]) == []


class TestGenericInspector:

    @pytest.fixture
    def metadata(self, shop_config):
        return inspect_generic("shop", shop_config)

    def table(self, metadata, name):
        return next(t for s in metadata.schemas for t in s.tables if t.name == name)

    def test_schemas_tables_and_views(self, metadata):
        assert [s.name for s in metadata.schemas] == ["main"]
        assert metadata.table_count == 4
        assert metadata.view_count == 1
        assert self.table(metadata, "big_orders").kind == "view"
        assert self.table(metadata, "customer").kind == "table"

    def test_columns_and_primary_keys(self, metadata):
        customer = self.table(metadata, "customer")

        assert [c.name for c in customer.columns] == ["id", "name", "status"]
        assert [c.name for c in customer.columns if c.primary_key] == ["id"]
        assert customer.columns[1].nullable == "NO"
        assert customer.columns[2].default is not None

    def test_foreign_keys_and_indexes(self, metadata):
        orders = self.table(metadata, "orders")
        [fk] = orders.foreign_keys

        assert (fk.column, fk.foreign_table, fk.foreign_column) == ("customer_id", "customer", "id")
        assert not fk.inferred
        assert [i.name for i in orders.indexes] == ["idx_orders_customer"]
        assert orders.indexes[0].columns == ["customer_id"]

    def test_dispatch_adds_inferred_relationships(self, shop_config):
        metadata = inspect_database("shop", shop_config)
        order_item = next(t for s in metadata.schemas for t in s.tables if t.name == "order_item")
        kinds = {fk.column: fk.inferred for fk in order_item.foreign_keys}

        assert kinds == {"order_id": False, "product_id": True}

    def test_documents_can_be_built(self, shop_config):
        documents = inspect_database("shop", shop_config).to_documents()

        assert {d.metadata["key"] for d in documents} == {
            "main.customer", "main.product", "main.orders", "main.order_item", "main.big_orders",
        }

    def test_configured_schemas_filter(self, shop_config):
        assert inspect_generic("shop", {**shop_config, "schemas": "main"}).table_count == 4


@pytest.mark.integration
class TestPostgresIntegration:
    """Runs against the configured PostgreSQL; enable with SQL_COPILOT_PG_TEST=1."""

    @pytest.fixture(autouse=True)
    def require_postgres(self):
        import os

        if not os.getenv("SQL_COPILOT_PG_TEST"):
            pytest.skip("set SQL_COPILOT_PG_TEST=1 to run PostgreSQL tests")

        from database.dialects import is_configured

        name = next((n for n, c in DATASOURCES.items() if is_configured(c)), None)
        if not name:
            pytest.skip("no configured PostgreSQL datasource")
        self.name = name

    def test_inspection_finds_tables_with_columns(self):
        metadata = inspect_database(self.name)

        assert metadata.table_count > 0
        assert all(t.columns for s in metadata.schemas for t in s.tables)

    def test_queries_run_in_a_read_only_transaction(self):
        result = execute_query(self.name, "SELECT current_setting('transaction_read_only') AS ro")

        assert result.rows["ro"][0] == "on"

    def test_ddl_is_refused_even_if_the_guard_were_bypassed(self):
        result = execute_query(self.name, "CREATE TEMP TABLE sql_copilot_probe (id int)")

        assert result.error

    def test_simple_select(self):
        assert execute_query(self.name, "SELECT 1 AS one").rows["one"][0] == 1
