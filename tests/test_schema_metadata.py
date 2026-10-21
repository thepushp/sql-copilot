from conftest import make_metadata, make_table
from database.schema_metadata import (
    ForeignKeyMetadata,
    infer_relationships,
)


def doc_for(metadata, key):
    return next(
        d for d in metadata.to_documents() if d.metadata["key"] == key
    )


class TestInferRelationships:

    def test_infers_entity_id_to_entity_id(self):
        student = make_table("student")
        calendar = make_table("student_calendar", ("id", "student_id"))
        metadata = make_metadata(("public", [student, calendar]))

        infer_relationships(metadata)

        [fk] = calendar.foreign_keys
        assert (fk.column, fk.foreign_table, fk.foreign_column) == (
            "student_id", "student", "id",
        )
        assert fk.inferred

    def test_does_not_duplicate_explicit_foreign_key(self):
        student = make_table("student")
        trip = make_table(
            "trip",
            ("id", "student_id"),
            foreign_keys=[
                ForeignKeyMetadata("student_id", "public", "student", "id")
            ],
        )
        metadata = make_metadata(("public", [student, trip]))

        infer_relationships(metadata)

        assert len(trip.foreign_keys) == 1
        assert not trip.foreign_keys[0].inferred

    def test_ignores_inactive_parent(self):
        student = make_table("student", flyway_managed=True, flyway_active=False)
        trip = make_table("trip", ("id", "student_id"))
        metadata = make_metadata(("public", [student, trip]))

        infer_relationships(metadata)

        assert trip.foreign_keys == []

    def test_requires_parent_in_same_schema(self):
        student = make_table("student", schema="a")
        trip = make_table("trip", ("id", "student_id"), schema="b")
        metadata = make_metadata(("a", [student]), ("b", [trip]))

        infer_relationships(metadata)

        assert trip.foreign_keys == []

    def test_requires_parent_id_column(self):
        student = make_table("student", ("code",), primary_key="code")
        trip = make_table("trip", ("id", "student_id"))
        metadata = make_metadata(("public", [student, trip]))

        infer_relationships(metadata)

        assert trip.foreign_keys == []

    def test_unknown_parent_is_skipped(self):
        trip = make_table("trip", ("id", "ghost_id"))
        metadata = make_metadata(("public", [trip]))

        infer_relationships(metadata)

        assert trip.foreign_keys == []


class TestCounts:

    def test_counts_exclude_views_from_tables(self):
        metadata = make_metadata(
            ("a", [make_table("t1"), make_table("v1", kind="view")]),
            ("b", [make_table("t2"), make_table("m1", kind="materialized view")]),
        )

        assert metadata.schema_count == 2
        assert metadata.table_count == 2
        assert metadata.view_count == 2


class TestDocuments:

    def test_inactive_tables_are_not_indexed(self, sample_metadata):
        keys = {d.metadata["key"] for d in sample_metadata.to_documents()}

        assert "public.old_table" not in keys
        assert "public.student" in keys

    def test_document_text_has_columns_and_relationships(self, sample_metadata):
        text = doc_for(sample_metadata, "public.trip").page_content

        assert "Table: trip" in text
        assert "Approx rows: 12,000" in text
        assert "Description: One row per trip" in text
        assert "common_values=[COMPLETED, CANCELLED]" in text
        assert "public.trip.student_id -> public.student.id" in text
        assert "explicit_foreign_key" in text

    def test_inferred_relationship_is_labelled(self, sample_metadata):
        text = doc_for(sample_metadata, "public.trip_log").page_content

        assert "type=inferred" in text

    def test_enum_values_are_labelled_allowed(self):
        table = make_table("t", ("id", "state"))
        table.columns[1].values = ["A", "B"]
        table.columns[1].values_kind = "enum"
        metadata = make_metadata(("public", [table]))

        assert "allowed_values=[A, B]" in doc_for(metadata, "public.t").page_content

    def test_views_are_marked(self):
        metadata = make_metadata(("public", [make_table("v", kind="view")]))

        assert "Type: view" in doc_for(metadata, "public.v").page_content

    def test_metadata_has_search_fields(self, sample_metadata):
        meta = doc_for(sample_metadata, "public.trip").metadata

        assert meta["columns"] == ["id", "student_id", "status"]
        assert "COMPLETED" in meta["values"]
        assert meta["kind"] == "table"
        assert meta["key"] == "public.trip"

    def test_search_text_is_readable(self, sample_metadata):
        search = doc_for(sample_metadata, "public.trip_log").metadata["search_text"]

        assert search.startswith("trip log in public")
        assert "trip id" in search

    def test_search_text_includes_values_and_comment(self, sample_metadata):
        search = doc_for(sample_metadata, "public.trip").metadata["search_text"]

        assert "One row per trip" in search
        assert "CANCELLED" in search

    def test_summary_line_has_values_and_rows(self, sample_metadata):
        summary = doc_for(sample_metadata, "public.trip").metadata["summary"]

        assert summary.startswith("public.trip:")
        assert "status=COMPLETED|CANCELLED" in summary
        assert "(~12,000 rows)" in summary
        assert "One row per trip" in summary

    def test_related_tables_are_symmetric(self, sample_metadata):
        trip = doc_for(sample_metadata, "public.trip").metadata["related"]
        student = doc_for(sample_metadata, "public.student").metadata["related"]

        assert "public.student" in trip
        assert "public.trip" in student

    def test_related_excludes_inactive_tables(self):
        parent = make_table("old", flyway_managed=True, flyway_active=False)
        child = make_table(
            "child",
            ("id", "old_id"),
            foreign_keys=[ForeignKeyMetadata("old_id", "public", "old", "id")],
        )
        metadata = make_metadata(("public", [parent, child]))

        assert doc_for(metadata, "public.child").metadata["related"] == []
