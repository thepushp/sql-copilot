import pytest
from langchain_core.documents import Document

from rag.context_builder import build_context
from rag.retriever import (
    DERIVED_PENALTY,
    _derived_factor,
    _keyword_score,
    add_related_tables,
    retrieve_candidates,
    retrieve_documents,
)
from rag.schema_indexer import build_schema_index
from rag.textutil import matches, overlap, variants, words


class TestTextUtil:

    def test_words_lowercases_and_drops_short_tokens(self):
        assert words("List ALL of the Students!") == {
            "list", "all", "the", "students",
        }

    @pytest.mark.parametrize(
        "first,second",
        [
            ("buses", "bus"),
            ("statuses", "status"),
            ("students", "student"),
            ("categories", "category"),
            ("trips", "trip"),
        ],
    )
    def test_plural_forms_match(self, first, second):
        assert matches(first, {second})
        assert matches(second, {first})

    @pytest.mark.parametrize("first,second", [("bus", "student"), ("trip", "strip")])
    def test_unrelated_words_do_not_match(self, first, second):
        assert not matches(first, {second})

    def test_short_words_are_not_stemmed(self):
        assert variants("bus") == {"bus"}
        assert variants("is") == {"is"}

    def test_overlap_counts_first_set_words(self):
        assert overlap({"trips", "cancelled"}, {"trip", "status"}) == 1
        assert overlap(set(), {"x"}) == 0


def doc(key, columns=(), values=(), related=(), content=None):
    return Document(
        page_content=content or f"FULL {key}",
        metadata={
            "key": key,
            "columns": list(columns),
            "values": list(values),
            "related": list(related),
            "content": content or f"FULL {key}",
            "search_text": f"{key} {' '.join(columns)}",
        },
    )


class TestKeywordScoring:

    def test_table_name_match_outweighs_column_match(self):
        question = words("show trips")
        by_table = _keyword_score(question, doc("public.trip", ["id"]))
        by_column = _keyword_score(question, doc("public.other", ["trip_id"]))

        assert by_table > by_column > 0

    def test_value_match_counts(self):
        question = words("cancelled things")
        with_value = _keyword_score(
            question, doc("public.t", ["status"], ["CANCELLED"])
        )

        assert with_value > 0

    def test_full_table_name_coverage_gets_bonus(self):
        question = words("student calendar entries")
        full = _keyword_score(question, doc("public.student_calendar"))
        partial = _keyword_score(question, doc("public.student_address"))

        assert full > partial

    def test_unrelated_question_scores_zero(self):
        assert _keyword_score(words("weather forecast"), doc("public.trip", ["id"])) == 0

    def test_derived_tables_are_penalised_unless_asked_for(self):
        derived = doc("public.trip_log")

        assert _derived_factor(words("show trips"), derived) == DERIVED_PENALTY
        assert _derived_factor(words("show the trip log"), derived) == 1.0
        assert _derived_factor(words("show trips"), doc("public.trip")) == 1.0

    def test_historical_schema_is_penalised(self):
        assert _derived_factor(
            words("show trips"), doc("historical.trip")
        ) == DERIVED_PENALTY


@pytest.fixture
def index(fake_embeddings):
    documents = [
        doc("public.trip", ["id", "status", "driver_id"], ["CANCELLED"], ["public.driver"]),
        doc("public.trip_log", ["id", "trip_id"]),
        doc("historical.trip", ["id", "status"]),
        doc("public.driver", ["id", "name"], related=["public.trip"]),
        doc("public.vehicle", ["id", "plate"]),
        doc("public.school", ["id", "name"]),
        doc("public.student", ["id", "name"]),
    ]
    return build_schema_index(documents, fake_embeddings)


class TestRetrieval:

    def test_index_embeds_search_text_but_keeps_full_text(self, index):
        stored = next(iter(index.docstore._dict.values()))

        assert stored.metadata["content"].startswith("FULL ")

    def test_keyword_match_ranks_first(self, index):
        keys = [d.metadata["key"] for d in retrieve_candidates(index, "cancelled trips", limit=3)]

        assert keys[0] == "public.trip"

    def test_derived_and_historical_rank_below_base_table(self, index):
        keys = [d.metadata["key"] for d in retrieve_candidates(index, "trips", limit=7)]

        assert keys.index("public.trip") < keys.index("public.trip_log")
        assert keys.index("public.trip") < keys.index("historical.trip")

    def test_limit_is_respected(self, index):
        assert len(retrieve_candidates(index, "anything", limit=2)) == 2

    def test_no_duplicate_candidates(self, index):
        keys = [d.metadata["key"] for d in retrieve_candidates(index, "trip driver", limit=7)]

        assert len(keys) == len(set(keys))

    def test_related_tables_are_added_up_to_the_cap(self, index):
        trip = [d for d in index.docstore._dict.values() if d.metadata["key"] == "public.trip"]

        expanded = add_related_tables(index, trip, max_neighbors=5)
        none = add_related_tables(index, trip, max_neighbors=0)

        assert [d.metadata["key"] for d in expanded] == ["public.trip", "public.driver"]
        assert len(none) == 1

    def test_related_tables_not_duplicated(self, index):
        both = [
            d for d in index.docstore._dict.values()
            if d.metadata["key"] in ("public.trip", "public.driver")
        ]

        assert len(add_related_tables(index, both, 5)) == 2

    def test_retrieve_documents_combines_both_steps(self, index):
        keys = [
            d.metadata["key"]
            for d in retrieve_documents(
                index, "cancelled trips", {"k": 1, "fetch_k": 7, "max_neighbors": 2}
            )
        ]

        assert keys[0] == "public.trip"
        assert "public.driver" in keys


class TestContextBuilder:

    def test_uses_full_text_from_metadata(self):
        assert build_context([doc("public.a")]) == "FULL public.a"

    def test_falls_back_to_page_content(self):
        plain = Document(page_content="plain text", metadata={})

        assert build_context([plain]) == "plain text"

    def test_sections_are_separated(self):
        context = build_context([doc("a.x"), doc("a.y")])

        assert context == "FULL a.x\n\n---\n\nFULL a.y"

    def test_indexes_can_be_stripped(self):
        full = "Table: t\nColumns:\n- id\nIndexes:\n- idx_t"
        document = doc("a.t", content=full)

        assert "Indexes:" in build_context([document], {"include_indexes": True})
        assert "Indexes:" not in build_context([document], {"include_indexes": False})

    def test_budget_truncates_but_keeps_first_table(self):
        documents = [doc(f"a.t{i}", content="x" * 100) for i in range(5)]

        assert build_context(documents, {"max_chars": 250}).count("x" * 100) == 2
        assert build_context(documents, {"max_chars": 10}).count("x" * 100) == 1
