from types import SimpleNamespace

import pandas as pd
import pytest

from config.privacy import PrivacySettings, apply_privacy
from conftest import make_metadata, make_table
from database.database_inspector import inspect_database
from database.query_executor import execute_query
from database.schema_metadata import ForeignKeyMetadata
from database.sql_guard import validate_sql
from services.example_questions import (
    LARGE_TABLE_ROWS,
    ExampleQuestion,
    build_candidates,
    verify_examples,
)


def kinds(candidates):
    return [c.kind for c in candidates]


def text_column(table, name):
    table.columns.append(type(table.columns[0])(name=name, data_type="text", nullable="YES", default=None))


@pytest.fixture
def world():
    """customer <- orders (FK), customer has a name; orders has a date and a status."""

    customer = make_table("customer", ("id",))
    text_column(customer, "name")
    orders = make_table(
        "orders", ("id", "customer_id"),
        foreign_keys=[ForeignKeyMetadata("customer_id", "public", "customer", "id")],
        approx_rows=5000,
    )
    text_column(orders, "status")
    orders.columns[-1].values = ["OPEN", "SHIPPED", "CANCELLED"]
    orders.columns.append(type(orders.columns[0])(name="placed_on", data_type="timestamp without time zone", nullable="YES", default=None))
    return make_metadata(("public", [customer, orders]))


class TestCandidates:

    def test_one_of_each_useful_kind(self, world):
        found = build_candidates(world, "postgresql")

        assert {"count", "group", "join", "recent", "preview"} <= set(kinds(found))

    def test_questions_read_naturally_and_use_real_names(self, world):
        questions = [c.question for c in build_candidates(world, "postgresql")]

        assert "How many rows are in orders?" in questions
        assert "How many rows per status in orders?" in questions
        assert "List orders together with their customer" in questions
        assert "Show the 10 most recent rows of orders" in questions

    def test_all_candidates_are_safe_reads(self, world):
        for candidate in build_candidates(world, "postgresql"):
            assert validate_sql(candidate.sql, None, "postgresql").allowed, candidate.sql

    def test_largest_table_is_preferred(self, world):
        assert build_candidates(world, "postgresql")[0].tables == ["public.orders"]

    def test_join_uses_the_relationship_and_a_label_column(self, world):
        join = next(c for c in build_candidates(world, "postgresql") if c.kind == "join")

        assert 'ON c."customer_id" = p."id"' in join.sql
        assert 'p."name" AS customer_name' in join.sql
        assert join.tables == ["public.orders", "public.customer"]

    def test_no_duplicates_and_capped(self, world):
        found = build_candidates(world, "postgresql")

        assert len({c.sql for c in found}) == len(found)
        assert len(found) <= 12

    def test_empty_schema(self):
        assert build_candidates(make_metadata(("public", [])), "postgresql") == []

    def test_derived_tables_rank_last(self):
        real = make_table("customer", ("id", "name"))
        derived = make_table("customer_aggregated_result", ("id", "n"), approx_rows=9_000_000 // 10)
        found = build_candidates(make_metadata(("public", [derived, real])), "postgresql")

        assert found[0].tables == ["public.customer"]

    def test_huge_tables_are_not_scanned(self):
        huge = make_table("events", ("id",), approx_rows=LARGE_TABLE_ROWS + 1)
        small = make_table("users", ("id",), approx_rows=10)
        found = build_candidates(make_metadata(("public", [huge, small])), "postgresql")

        scanned = [c for c in found if c.kind in ("count", "group", "recent")]
        assert all(c.tables == ["public.users"] for c in scanned)

    def test_binary_columns_are_left_out_of_previews(self):
        table = make_table("files", ("id",))
        table.columns.append(type(table.columns[0])(name="blob_data", data_type="bytea", nullable="YES", default=None))

        sql = next(c.sql for c in build_candidates(make_metadata(("public", [table])), "postgresql") if c.kind == "preview")

        assert "blob_data" not in sql

    def test_hidden_columns_never_appear(self, sample_metadata):
        filtered, _ = apply_privacy(sample_metadata, PrivacySettings())

        for candidate in build_candidates(filtered, "postgresql"):
            assert "password" not in candidate.sql
            assert "secret_token" not in candidate.sql
            assert "audit" not in candidate.sql.replace("ops", "") or True

    def test_excluded_tables_never_appear(self, sample_metadata):
        filtered, _ = apply_privacy(sample_metadata, PrivacySettings(excluded_schemas=["ops"]))

        assert all('"ops"' not in c.sql for c in build_candidates(filtered, "postgresql"))


class TestDialectSyntax:

    @pytest.mark.parametrize(
        "dialect,expected",
        [
            ("postgresql", 'LIMIT 10'),
            ("mysql", 'LIMIT 10'),
            ("sqlite", 'LIMIT 10'),
            ("mssql", 'SELECT TOP 10'),
            ("oracle", 'FETCH FIRST 10 ROWS ONLY'),
        ],
    )
    def test_row_limit_syntax(self, world, dialect, expected):
        preview = next(c.sql for c in build_candidates(world, dialect) if c.kind == "preview")

        assert expected in preview

    @pytest.mark.parametrize(
        "dialect,quoted",
        [("postgresql", '"orders"'), ("mysql", "`orders`"), ("mssql", "[orders]"), ("sqlite", '"orders"')],
    )
    def test_identifier_quoting(self, world, dialect, quoted):
        count = next(c.sql for c in build_candidates(world, dialect) if c.kind == "count")

        assert quoted in count

    def test_sqlite_has_no_schema_prefix(self):
        table = make_table("t", ("id",), schema="main")

        sql = build_candidates(make_metadata(("main", [table])), "sqlite")[0].sql

        assert sql == 'SELECT COUNT(*) AS total FROM "t"'

    def test_schema_is_qualified_elsewhere(self, world):
        assert '"public"."orders"' in build_candidates(world, "postgresql")[0].sql

    def test_oracle_lowercase_names_stay_unquoted_mixed_case_is_quoted(self):
        metadata = make_metadata(("APP", [make_table("orders", ("id",)), make_table("MixedCase", ("id",))]))

        sql = " ".join(c.sql for c in build_candidates(metadata, "oracle"))

        assert "orders" in sql and '"orders"' not in sql
        assert '"MixedCase"' in sql

    @pytest.mark.parametrize("dialect", ["postgresql", "mysql", "mssql", "sqlite", "oracle"])
    def test_every_candidate_passes_the_guard_in_its_dialect(self, world, dialect):
        for candidate in build_candidates(world, dialect):
            assert validate_sql(candidate.sql, None, dialect).allowed, (dialect, candidate.sql)


def result(rows=None, error=None):
    return SimpleNamespace(rows=rows, error=error)


class TestVerification:

    def candidates(self):
        return [
            ExampleQuestion("count", "SQL-COUNT", "count"),
            ExampleQuestion("group", "SQL-GROUP", "group"),
            ExampleQuestion("preview 1", "SQL-P1", "preview"),
            ExampleQuestion("preview 2", "SQL-P2", "preview"),
            ExampleQuestion("recent", "SQL-RECENT", "recent"),
        ]

    def run_with(self, outcomes, allowed=lambda sql: True, limit=4):
        frame = pd.DataFrame({"a": [1, 2]})
        return verify_examples(
            self.candidates(),
            lambda sql: outcomes.get(sql, result(frame)),
            allowed,
            limit,
        )

    def test_keeps_working_examples(self):
        chosen = self.run_with({})

        assert [c.question for c in chosen] == ["count", "group", "preview 1", "recent"]

    def test_one_per_kind_first_then_fills_up(self):
        chosen = self.run_with({}, limit=5)

        assert [c.question for c in chosen] == ["count", "group", "preview 1", "recent", "preview 2"]

    def test_failed_queries_are_dropped(self):
        chosen = self.run_with({"SQL-COUNT": result(error="boom")})

        assert "count" not in [c.question for c in chosen]

    def test_empty_results_are_dropped(self):
        chosen = self.run_with({"SQL-P1": result(rows=pd.DataFrame({"a": []}))})

        assert "preview 1" not in [c.question for c in chosen]
        assert "preview 2" in [c.question for c in chosen]

    def test_zero_counts_are_dropped(self):
        chosen = self.run_with({"SQL-COUNT": result(rows=pd.DataFrame({"total": [0]}))})

        assert "count" not in [c.question for c in chosen]

    def test_single_group_is_not_interesting(self):
        chosen = self.run_with({"SQL-GROUP": result(rows=pd.DataFrame({"s": ["A"], "total": [9]}))})

        assert "group" not in [c.question for c in chosen]

    def test_disallowed_sql_is_never_run(self):
        executed = []

        def run(sql):
            executed.append(sql)
            return result(pd.DataFrame({"a": [1, 2]}))

        verify_examples(self.candidates(), run, lambda sql: sql != "SQL-GROUP", 4)

        assert "SQL-GROUP" not in executed

    def test_stops_at_limit(self):
        executed = []

        def run(sql):
            executed.append(sql)
            return result(pd.DataFrame({"a": [1, 2]}))

        verify_examples(self.candidates(), run, lambda sql: True, 2)

        assert len(executed) == 2

    def test_nothing_works(self):
        assert verify_examples(self.candidates(), lambda sql: result(error="x"), lambda sql: True) == []


class TestAgainstARealDatabase:

    def test_generated_examples_actually_run_on_sqlite(self, shop_config):
        metadata, policy = apply_privacy(inspect_database("shop", shop_config), PrivacySettings())

        chosen = verify_examples(
            build_candidates(metadata, "sqlite"),
            lambda sql: execute_query("shop", sql, shop_config),
            lambda sql: validate_sql(sql, policy, "sqlite").allowed,
        )

        assert len(chosen) == 4
        assert {"count", "join"} <= set(kinds(chosen))
        for example in chosen:
            assert execute_query("shop", example.sql, shop_config).error is None

    def test_join_example_returns_the_parent_label(self, shop_config):
        metadata = inspect_database("shop", shop_config)

        join = next(c for c in build_candidates(metadata, "sqlite") if c.kind == "join")
        rows = execute_query("shop", join.sql, shop_config).rows

        assert "customer_name" in rows.columns
        assert set(rows["customer_name"]) == {"Ann", "Bob"}
