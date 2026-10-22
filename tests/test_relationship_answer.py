from database.schema_metadata import (
    DatabaseMetadata,
    ForeignKeyMetadata,
    SchemaMetadata,
    TableMetadata,
)
from services.relationship_answer import (
    answer_relationship_question,
    is_relationship_question,
)


def _metadata():
    trf = TableMetadata(schema="intake", name="trf")
    student = TableMetadata(
        schema="intake",
        name="trf_student",
        foreign_keys=[ForeignKeyMetadata("trf_id", "intake", "trf", "id")],
    )
    school = TableMetadata(schema="intake", name="trf_school")
    return DatabaseMetadata(
        schemas=[SchemaMetadata("intake", [trf, student, school])]
    )


def test_detects_relationship_questions():
    assert is_relationship_question("how is trf table connected to other table")
    assert not is_relationship_question("how many trf are connected to a school")
    assert not is_relationship_question("show orders from last week")


def test_single_table_lists_both_directions():
    text = answer_relationship_question(
        "how is trf table connected to other table", _metadata()
    )
    assert "intake.trf_student.trf_id" in text
    assert "intake.trf_school" not in text


def test_two_tables_direct_link():
    text = answer_relationship_question(
        "how are trf and trf_student related", _metadata()
    )
    assert "directly connected" in text


def test_overview_without_table_name():
    text = answer_relationship_question(
        "how are the tables connected", _metadata()
    )
    assert "intake.trf_student.trf_id" in text


def test_non_relationship_returns_none():
    assert answer_relationship_question("list schools", _metadata()) is None


def test_which_table_stores_lookup():
    from database.schema_metadata import ColumnMetadata
    from services.schema_answer import answer_schema_question

    metadata = _metadata()
    school = metadata.schemas[0].tables[2]
    school.columns = [ColumnMetadata("school_name", "text", "YES", None)]
    school.approx_rows = 0

    text = answer_schema_question("which table stores school names?", metadata)
    assert "intake.trf_school" in text
    assert "school_name" in text
    assert "about 0 rows" in text

    assert answer_schema_question("count schools by district", metadata) is None
