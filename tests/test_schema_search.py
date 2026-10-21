import pytest

from conftest import make_metadata, make_table
from services.schema_search import search_metadata


@pytest.fixture
def metadata():
    student = make_table("student", ("id", "name", "school_id", "status"))
    student.comment = "People who attend a school"
    student.columns[3].values = ["ACTIVE", "DROPPED"]
    student_trip = make_table("student_trip", ("id", "student_id", "route_code"))
    trip = make_table("trip", ("id", "driver_id", "status"))
    trip.columns[2].values = ["CANCELLED", "COMPLETED"]
    trip.columns[1].comment = "The driver who ran the trip"
    historical_trip = make_table("trip", ("id", "driver_id"), schema="historical")
    school = make_table("school", ("id", "name"))
    return make_metadata(
        ("public", [student, student_trip, trip, school]),
        ("historical", [historical_trip]),
    )


def keys(hits):
    return [h.key for h in hits]


class TestMatching:

    def test_exact_name_ranks_first(self, metadata):
        assert keys(search_metadata(metadata, "trip"))[:2] == ["historical.trip", "public.trip"]

    def test_prefix_beats_a_match_in_the_middle(self, metadata):
        hits = keys(search_metadata(metadata, "stud"))

        assert hits.index("public.student") < hits.index("public.student_trip")

    def test_part_of_a_name_matches(self, metadata):
        assert "public.student_trip" in keys(search_metadata(metadata, "udent_tr"))

    def test_plurals_match(self, metadata):
        assert "public.student" in keys(search_metadata(metadata, "students"))
        assert "public.school" in keys(search_metadata(metadata, "schools"))

    def test_case_is_ignored(self, metadata):
        assert keys(search_metadata(metadata, "STUDENT")) == keys(search_metadata(metadata, "student"))

    def test_schema_qualified_search(self, metadata):
        assert keys(search_metadata(metadata, "historical.trip")) == ["historical.trip"]
        assert keys(search_metadata(metadata, "public.trip"))[0] == "public.trip"

    def test_column_names_match(self, metadata):
        hits = search_metadata(metadata, "route_code")

        assert keys(hits) == ["public.student_trip"]
        assert hits[0].reasons == [("column", "route_code")]

    def test_comments_match(self, metadata):
        hits = search_metadata(metadata, "attend")

        assert keys(hits) == ["public.student"]
        assert hits[0].reasons[0][0] == "comment"

    def test_column_comments_match(self, metadata):
        assert "public.trip" in keys(search_metadata(metadata, "ran the trip"))

    def test_known_values_match(self, metadata):
        hits = search_metadata(metadata, "cancel")

        assert keys(hits) == ["public.trip"]
        assert hits[0].reasons == [("value", "status = CANCELLED")]

    def test_every_word_must_match(self, metadata):
        assert keys(search_metadata(metadata, "student cancelled")) == []

    def test_words_can_match_in_different_places(self, metadata):
        hits = keys(search_metadata(metadata, "trip cancelled"))

        assert hits == ["public.trip"]

    def test_nothing_matches(self, metadata):
        assert search_metadata(metadata, "zzzz") == []

    @pytest.mark.parametrize("query", ["", "   ", "\t"])
    def test_blank_queries_return_nothing(self, metadata, query):
        assert search_metadata(metadata, query) == []


class TestResults:

    def test_limit(self, metadata):
        assert len(search_metadata(metadata, "id", limit=2)) == 2

    def test_order_is_stable(self, metadata):
        assert keys(search_metadata(metadata, "id")) == keys(search_metadata(metadata, "id"))

    def test_summary_is_readable(self, metadata):
        hit = search_metadata(metadata, "cancel")[0]

        assert hit.summary() == "value: status = CANCELLED"

    def test_reasons_are_capped(self, metadata):
        hit = next(h for h in search_metadata(metadata, "student") if h.key == "public.student")

        assert len(hit.reasons) <= 3

    def test_only_the_metadata_given_is_searched(self, metadata):
        metadata.schemas = [s for s in metadata.schemas if s.name != "historical"]

        assert "historical.trip" not in keys(search_metadata(metadata, "trip"))
