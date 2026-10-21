import pytest
from langchain_core.documents import Document

from services import glossary as glossary_module
from services.glossary import (
    MAX_GLOSSARY_CHARS,
    expand_query,
    load_glossary,
    save_glossary,
)
from services.table_selector import _parse_keys, select_tables


VALID = ["public.student", "public.school", "hr.x"]


class TestParseKeys:

    def test_json_array(self):
        assert _parse_keys('["public.school", "public.student"]', VALID) == [
            "public.school", "public.student",
        ]

    def test_fenced_and_case_insensitive(self):
        text = '```json\n["PUBLIC.School", "nope"]\n```'

        assert _parse_keys(text, VALID) == ["public.school"]

    def test_duplicates_removed_order_kept(self):
        text = '["public.student", "public.school", "public.student"]'

        assert _parse_keys(text, VALID) == ["public.student", "public.school"]

    def test_falls_back_to_names_mentioned_in_prose(self):
        assert _parse_keys("I would use public.student and hr.x", VALID) == [
            "public.student", "hr.x",
        ]

    def test_empty_array_means_nothing_chosen(self):
        assert _parse_keys("[]", VALID) == []

    def test_think_blocks_are_ignored(self):
        text = '<think>maybe public.school</think>["public.student"]'

        assert _parse_keys(text, VALID) == ["public.student"]

    def test_garbage(self):
        assert _parse_keys("no idea", VALID) == []


def candidate(key):
    return Document(
        page_content=key,
        metadata={"key": key, "summary": f"{key}: id, name [status=A|B]"},
    )


class TestSelectTables:

    def test_returns_chosen_documents_and_usage(self, scripted_llm):
        model = scripted_llm(lambda p, n: '["public.school"]')
        candidates = [candidate("public.student"), candidate("public.school")]

        chosen, tokens_in, tokens_out = select_tables(
            model, "which schools?", candidates, history_text="User: hi",
            glossary="bus: vehicle", max_tables=5,
        )

        assert [d.metadata["key"] for d in chosen] == ["public.school"]
        assert (tokens_in, tokens_out) == (100, 10)

    def test_prompt_lists_summaries_glossary_and_history(self, scripted_llm):
        model = scripted_llm(lambda p, n: "[]")

        select_tables(
            model, "Q-MARK", [candidate("public.student")],
            history_text="HIST-MARK", glossary="GLOSS-MARK", max_tables=4,
        )
        prompt = model.calls[0]

        for marker in ("Q-MARK", "HIST-MARK", "GLOSS-MARK", "public.student: id, name [status=A|B]", "at most 4"):
            assert marker in prompt

    def test_result_is_capped(self, scripted_llm):
        keys = [f"public.t{i}" for i in range(6)]
        model = scripted_llm(lambda p, n: str(keys).replace("'", '"'))

        chosen, _, _ = select_tables(model, "q", [candidate(k) for k in keys], max_tables=3)

        assert len(chosen) == 3

    def test_unknown_names_are_dropped(self, scripted_llm):
        model = scripted_llm(lambda p, n: '["public.ghost"]')

        chosen, _, _ = select_tables(model, "q", [candidate("public.student")])

        assert chosen == []


GLOSSARY = (
    "bus: vehicle table\n"
    "route = run (a trip's path)\n"
    "active students -> student where status = 'ACTIVE'\n"
    "no separator line\n"
    "empty term:\n"
)


class TestGlossary:

    @pytest.mark.parametrize(
        "question,addition",
        [
            ("which buses are late", "vehicle table"),
            ("list routes", "run (a trip's path)"),
            ("show active student names", "student where status = 'ACTIVE'"),
        ],
    )
    def test_terms_expand_the_query(self, question, addition):
        assert expand_query(question, GLOSSARY) == f"{question} {addition}"

    def test_multi_word_term_needs_all_words(self):
        assert expand_query("show students", GLOSSARY) == "show students"

    def test_no_match_or_empty_glossary(self):
        assert expand_query("hello world", GLOSSARY) == "hello world"
        assert expand_query("anything", "") == "anything"
        assert expand_query("anything", "   \n") == "anything"

    def test_malformed_lines_are_ignored(self):
        assert expand_query("no separator line", GLOSSARY) == "no separator line"

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(glossary_module, "GLOSSARY_PATH", tmp_path / "d" / "g.txt")

        assert load_glossary() == ""

        save_glossary("  bus: vehicle  ")

        assert load_glossary() == "bus: vehicle\n"

    def test_saved_glossary_is_capped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(glossary_module, "GLOSSARY_PATH", tmp_path / "g.txt")

        save_glossary("x" * (MAX_GLOSSARY_CHARS + 500))

        assert len(load_glossary()) <= MAX_GLOSSARY_CHARS
