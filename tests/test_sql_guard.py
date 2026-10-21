import pytest

from config.privacy import AccessPolicy
from database.sql_guard import classify_sql, validate_sql


ALLOWED = [
    ("postgresql", "SELECT 1"),
    ("postgresql", "WITH x AS (SELECT 1 AS a) SELECT * FROM x"),
    ("postgresql", "SELECT a::int FROM t WHERE b = 'x:y'"),
    ("postgresql", "SELECT count(*) FROM t"),
    ("mysql", "SELECT * FROM `t` LIMIT 5"),
    ("mssql", "SELECT TOP 50 * FROM dbo.t"),
    ("mssql", "SELECT * FROM [dbo].[t]"),
    ("sqlite", "SELECT * FROM t LIMIT 5"),
    ("oracle", "SELECT * FROM t FETCH FIRST 50 ROWS ONLY"),
    ("oracle", "SELECT * FROM t WHERE ROWNUM <= 5"),
]

REJECTED = [
    ("postgresql", "DELETE FROM t"),
    ("postgresql", "INSERT INTO t VALUES (1)"),
    ("postgresql", "UPDATE t SET a = 1"),
    ("postgresql", "DROP TABLE t"),
    ("postgresql", "TRUNCATE t"),
    ("postgresql", "SELECT 1; SELECT 2"),
    ("postgresql", "WITH d AS (DELETE FROM a RETURNING *) SELECT * FROM d"),
    ("postgresql", "SELECT * INTO new_t FROM t"),
    ("postgresql", "SELECT * FROM t FOR UPDATE"),
    ("postgresql", "SELECT pg_sleep(5)"),
    ("postgresql", "SELECT pg_read_file('/etc/passwd')"),
    ("mysql", "SELECT SLEEP(5)"),
    ("mysql", "SELECT * INTO OUTFILE '/tmp/x' FROM t"),
    ("mysql", "DELETE FROM t"),
    ("mssql", "EXEC xp_cmdshell 'dir'"),
    ("mssql", "WAITFOR DELAY '00:00:05'"),
    ("mssql", "SELECT * INTO #x FROM t"),
    ("sqlite", "SELECT load_extension('x')"),
    ("sqlite", "DROP TABLE t"),
    ("sqlite", "PRAGMA writable_schema = 1"),
    ("oracle", "DELETE FROM t"),
    ("oracle", "SELECT * FROM t FOR UPDATE"),
]


@pytest.mark.parametrize("dialect,sql", ALLOWED)
def test_read_queries_are_allowed(dialect, sql):
    assert validate_sql(sql, None, dialect).allowed


@pytest.mark.parametrize("dialect,sql", REJECTED)
def test_unsafe_queries_are_rejected(dialect, sql):
    result = validate_sql(sql, None, dialect)

    assert not result.allowed
    assert result.reason


@pytest.mark.parametrize("sql", ["", "   ", "SELEC nonsense ((("])
def test_empty_and_invalid_sql_are_rejected(sql):
    assert not validate_sql(sql).allowed


def test_nested_write_is_reported_as_data_modifying():
    result = validate_sql(
        "WITH d AS (DELETE FROM a RETURNING *) SELECT * FROM d"
    )

    assert "Data-modifying" in result.reason


@pytest.mark.parametrize(
    "sql,dialect,expected",
    [
        ("SELECT 1", "postgresql", "READ"),
        ("SELECT TOP 5 * FROM t", "mssql", "READ"),
        ("SELECT 1 UNION SELECT 2", "postgresql", "READ"),
        ("INSERT INTO t VALUES (1)", "mysql", "DML"),
        ("UPDATE t SET a = 1", "postgresql", "DML"),
        ("DROP TABLE t", "oracle", "DDL"),
        ("TRUNCATE TABLE t", "postgresql", "DDL"),
        ("CREATE TABLE t (a int)", "postgresql", "DDL"),
        ("EXEC sp_who", "mssql", "OTHER"),
    ],
)
def test_classify_sql(sql, dialect, expected):
    assert classify_sql(sql, dialect) == expected


def test_multiple_statements_raise_on_classify():
    with pytest.raises(ValueError):
        classify_sql("SELECT 1; SELECT 2")


class TestAccessPolicy:

    policy = AccessPolicy(
        blocked_tables={"hr.salary"},
        blocked_columns={"password"},
        star_restricted={"public.users"},
    )

    def check(self, sql):
        return validate_sql(sql, self.policy)

    def test_blocked_table_qualified(self):
        assert not self.check("SELECT id FROM hr.salary").allowed

    def test_blocked_table_unqualified_matches_by_name(self):
        assert not self.check("SELECT id FROM salary").allowed

    def test_same_table_name_in_other_schema_is_fine(self):
        assert self.check("SELECT id FROM public.salary").allowed

    def test_blocked_table_in_join(self):
        sql = "SELECT u.id FROM public.users u JOIN hr.salary s ON s.id = u.id"
        assert not self.check(sql).allowed

    def test_blocked_column(self):
        assert not self.check("SELECT password FROM public.users").allowed

    def test_blocked_column_in_where_clause(self):
        sql = "SELECT id FROM public.users WHERE password = 'x'"
        assert not self.check(sql).allowed

    def test_star_on_restricted_table(self):
        assert not self.check("SELECT * FROM public.users").allowed

    def test_qualified_star_on_restricted_table(self):
        assert not self.check("SELECT u.* FROM public.users u").allowed

    def test_count_star_is_not_a_star_violation(self):
        assert self.check("SELECT count(*) FROM public.users").allowed

    def test_explicit_columns_on_restricted_table(self):
        assert self.check("SELECT id, name FROM public.users").allowed

    def test_cte_with_blocked_name_is_not_the_table(self):
        sql = "WITH salary AS (SELECT 1 AS x) SELECT * FROM salary"
        assert self.check(sql).allowed

    def test_no_policy_means_no_restrictions(self):
        assert validate_sql("SELECT password FROM hr.salary").allowed
