from services.sql_qualify import qualify_tables, tables_in_sql


def test_bare_tables_get_their_schema(sample_metadata):
    sql = qualify_tables(
        "SELECT s.name FROM student AS s JOIN trip t ON t.student_id = s.id WHERE t.status ILIKE '%x%' LIMIT 5",
        sample_metadata,
        "postgresql",
    )
    assert "public.student AS s" in sql and "public.trip AS t" in sql
    assert "ILIKE" in sql and "LIMIT 5" in sql


def test_already_qualified_ctes_and_unknown_tables_are_left_alone(sample_metadata):
    sql = "WITH x AS (SELECT * FROM public.student) SELECT * FROM x JOIN mystery ON TRUE"
    assert qualify_tables(sql, sample_metadata, "postgresql") == sql


def test_ambiguous_names_are_not_guessed(sample_metadata):
    from conftest import make_table
    sample_metadata.schemas[0].tables.append(make_table("student"))
    sql = "SELECT * FROM student"
    assert qualify_tables(sql, sample_metadata, "postgresql") == sql


def test_unchanged_sql_is_returned_verbatim(sample_metadata):
    sql = "SELECT  *  FROM public.student"
    assert qualify_tables(sql, sample_metadata, "postgresql") == sql


def test_sqlite_and_bad_sql_are_untouched(sample_metadata):
    assert qualify_tables("SELECT * FROM student", sample_metadata, "sqlite") == "SELECT * FROM student"
    assert qualify_tables("not sql ((", sample_metadata, "postgresql") == "not sql (("


def test_tables_in_sql(sample_metadata):
    assert tables_in_sql(
        "SELECT * FROM student s JOIN public.trip t ON TRUE JOIN nope", sample_metadata, "postgresql"
    ) == ["public.student", "public.trip"]


def test_format_sql_is_multiline_and_equivalent():
    from services.sql_qualify import format_sql

    out = format_sql("select a,b from t where x=1 and y=2 order by a limit 5", "postgresql")
    assert out.count("\n") >= 4
    assert out.startswith("SELECT") and "LIMIT 5" in out
    assert format_sql("not sql ((", "postgresql") == "not sql (("


def test_finalize_qualifies_then_formats(sample_metadata):
    from services.sql_qualify import finalize_sql

    out = finalize_sql("select name from student where id=1", sample_metadata, "postgresql")
    assert "public.student" in out and "\n" in out
