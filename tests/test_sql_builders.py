import pytest

from conftest import make_table
from database.schema_metadata import ForeignKeyMetadata
from database.sql_guard import validate_sql
from services import sql_builders as sb

DIALECTS = ["postgresql", "mysql", "mssql", "sqlite", "oracle"]


@pytest.fixture
def orders():
    return make_table("orders", ("id", "customer_id", "total", "note"))


@pytest.fixture
def log():
    return make_table("log", ("ts", "msg"), primary_key=None)


class TestQuoting:

    @pytest.mark.parametrize(
        "dialect,expected",
        [
            ("postgresql", '"public"."orders"'),
            ("mysql", "`public`.`orders`"),
            ("mssql", "[public].[orders]"),
            ("sqlite", '"orders"'),
        ],
    )
    def test_qualified_name(self, orders, dialect, expected):
        assert sb.qualified_name(orders, dialect) == expected

    def test_oracle_lowercase_names_are_left_unquoted(self):
        assert sb.to_sql(sb.ident("orders", "oracle"), "oracle") == "orders"

    def test_oracle_mixed_case_and_odd_names_are_quoted(self):
        assert sb.to_sql(sb.ident("MixedCase", "oracle"), "oracle") == '"MixedCase"'
        assert sb.to_sql(sb.ident("has space", "oracle"), "oracle") == '"has space"'

    def test_embedded_quotes_cannot_break_out(self):
        text = sb.to_sql(sb.ident('a"; DROP TABLE x; --', "postgresql"), "postgresql")

        assert text.startswith('"') and text.endswith('"')
        assert text.count('""') == 1, "the inner quote is doubled, not left as a terminator"


class TestColumns:

    def test_binary_and_geometry_columns_are_not_shown(self):
        table = make_table("files", ("id", "name", "payload", "shape", "doc"))
        types = {"payload": "bytea", "shape": "geometry(Point,4326)", "doc": "jsonb", "name": "text"}
        for column in table.columns:
            column.data_type = types.get(column.name, "bigint")

        assert [c.name for c in sb.displayable_columns(table)] == ["id", "name", "doc"]

    def test_label_column_prefers_a_name_like_text_column(self):
        table = make_table("customer", ("id", "email", "name"))
        for column in table.columns:
            column.data_type = "text" if column.name != "id" else "bigint"

        assert sb.label_column(table).name == "name"

    def test_label_column_falls_back_to_any_text_column(self):
        table = make_table("t", ("id", "ref_id", "city"))
        table.columns[2].data_type = "character varying(50)"

        assert sb.label_column(table).name == "city"

    def test_label_column_none_when_nothing_readable(self):
        assert sb.label_column(make_table("t", ("id", "qty"))) is None


class TestFilters:

    @pytest.mark.parametrize(
        "text",
        [
            "status = 'OPEN'",
            "total > 5 AND note IS NULL",
            "name LIKE 'A%'",
            "id IN (1, 2, 3)",
            "created >= '2026-01-01' OR flag",
            "status = 'OPEN';",
            "  total BETWEEN 1 AND 9  ",
        ],
    )
    def test_conditions_are_accepted(self, text):
        assert sb.parse_filter(text, "postgresql") is not None

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_empty_means_no_filter(self, text):
        assert sb.parse_filter(text, "postgresql") is None

    @pytest.mark.parametrize(
        "text",
        [
            "DELETE FROM orders",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1",
            "DROP TABLE t",
            "SELECT 1",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "1 = 1; DROP TABLE orders",
            "a = 1; b = 2",
            "status ==== 'x'",
            "((",
        ],
    )
    def test_statements_and_garbage_are_rejected(self, text):
        with pytest.raises(ValueError):
            sb.parse_filter(text, "postgresql")

    def test_a_filter_can_only_ever_end_up_in_the_where_clause(self, orders):
        where = sb.parse_filter("total > 5", "postgresql")
        sql = sb.page_query(orders, "postgresql", where=where, limit=10)

        assert sql.count("WHERE") == 1
        assert sql.index("FROM") < sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")

    @pytest.mark.parametrize(
        "text",
        [
            "total > (SELECT pg_sleep(5))",
            "id IN (SELECT id FROM pg_catalog.pg_tables)",
            "pg_advisory_lock(1) = 1",
        ],
    )
    def test_sneaky_conditions_are_stopped_by_the_safety_check(self, orders, text):
        sql = sb.page_query(orders, "postgresql", where=sb.parse_filter(text, "postgresql"))

        assert not validate_sql(sql, None, "postgresql").allowed


class TestPaging:

    def test_page_query_names_every_column(self, orders):
        sql = sb.page_query(orders, "postgresql", limit=10)

        assert "*" not in sql
        assert sql.startswith('SELECT "id", "customer_id", "total", "note" FROM "public"."orders"')

    def test_hidden_binary_columns_are_left_out(self):
        table = make_table("files", ("id", "payload"))
        table.columns[1].data_type = "bytea"

        assert "payload" not in sb.page_query(table, "postgresql")

    @pytest.mark.parametrize(
        "dialect,fragment",
        [
            ("postgresql", "LIMIT 50"),
            ("mysql", "LIMIT 50"),
            ("sqlite", "LIMIT 50"),
            ("mssql", "SELECT TOP 50"),
            ("oracle", "FETCH FIRST 50 ROWS ONLY"),
        ],
    )
    def test_first_page_row_limit(self, orders, dialect, fragment):
        assert fragment in sb.page_query(orders, dialect, limit=50)

    @pytest.mark.parametrize(
        "dialect,fragment",
        [
            ("postgresql", "LIMIT 50 OFFSET 100"),
            ("mysql", "LIMIT 50 OFFSET 100"),
            ("sqlite", "LIMIT 50 OFFSET 100"),
            ("mssql", "OFFSET 100 ROWS FETCH FIRST 50 ROWS ONLY"),
            ("oracle", "OFFSET 100 ROWS FETCH FIRST 50 ROWS ONLY"),
        ],
    )
    def test_later_pages_use_offset(self, orders, dialect, fragment):
        assert fragment in sb.page_query(orders, dialect, limit=50, offset=100)

    def test_default_order_is_the_primary_key_so_pages_do_not_overlap(self, orders):
        assert 'ORDER BY "id" ASC' in sb.page_query(orders, "postgresql", limit=5)

    def test_composite_primary_keys_are_all_used(self):
        table = make_table("link", ("a_id", "b_id", "note"))
        table.columns[0].primary_key = table.columns[1].primary_key = True

        assert 'ORDER BY "a_id" ASC, "b_id" ASC' in sb.page_query(table, "postgresql")

    def test_no_primary_key_means_no_order_on_the_first_page(self, log):
        assert "ORDER BY" not in sb.page_query(log, "postgresql", limit=5)

    @pytest.mark.parametrize("dialect", ["mssql", "oracle"])
    def test_these_databases_need_an_order_to_page(self, log, dialect):
        sql = sb.page_query(log, dialect, limit=5, offset=5)

        assert "ORDER BY" in sql and "OFFSET 5 ROWS" in sql

    def test_explicit_order_wins(self, orders):
        sql = sb.page_query(orders, "postgresql", order_by=[("total", True), ("id", False)])

        assert 'ORDER BY "total" DESC, "id" ASC' in sql

    @pytest.mark.parametrize("dialect", DIALECTS)
    def test_order_by_stays_plain_so_indexes_can_be_used(self, orders, dialect):
        for order in (None, [("total", True)], [("total", False)]):
            sql = sb.page_query(orders, dialect, order_by=order, limit=5)

            assert "CASE WHEN" not in sql, sql

    @pytest.mark.parametrize("dialect", DIALECTS)
    def test_every_page_query_passes_the_safety_check(self, orders, dialect):
        where = sb.parse_filter("total > 5 AND note IS NOT NULL", dialect)

        for offset in (0, 100):
            sql = sb.page_query(orders, dialect, where=where, limit=50, offset=offset)

            assert validate_sql(sql, None, dialect).allowed, sql

    def test_limit_is_forced_to_a_sensible_number(self, orders):
        assert "LIMIT 1" in sb.page_query(orders, "postgresql", limit=0)

    def test_count_query(self, orders):
        where = sb.parse_filter("total > 5", "postgresql")

        assert sb.count_query(orders, "postgresql") == 'SELECT COUNT(*) AS total FROM "public"."orders"'
        assert sb.count_query(orders, "postgresql", where).endswith("WHERE total > 5")

    @pytest.mark.parametrize("dialect", DIALECTS)
    def test_count_query_passes_the_safety_check(self, orders, dialect):
        assert validate_sql(sb.count_query(orders, dialect), None, dialect).allowed


class TestJoin:

    def test_join_query(self):
        parent = make_table("customer", ("id", "name"))
        parent.columns[1].data_type = "text"
        child = make_table("orders", ("id", "customer_id", "total"))
        fk = ForeignKeyMetadata("customer_id", "public", "customer", "id")

        sql = sb.join_query(child, fk, parent, sb.label_column(parent), "postgresql", 25)

        assert 'ON c."customer_id" = p."id"' in sql
        assert 'p."name" AS customer_name' in sql
        assert sql.endswith("LIMIT 25")
        assert validate_sql(sql, None, "postgresql").allowed
