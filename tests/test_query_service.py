import pandas as pd
import pytest

from prompts.sql_prompt import CLARIFY_PREFIX
from services.query_service import (
    NeedsClarification,
    NoSQLGenerated,
    build_history,
    clean_sql,
    process_question,
    repair_question,
    summarize_result,
)


class TestCleanSql:

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("SELECT 1", "SELECT 1"),
            ("SELECT 1;", "SELECT 1"),
            ("  SELECT 1 ;  \n", "SELECT 1"),
            ("```sql\nSELECT 1\n```", "SELECT 1"),
            ("```\nSELECT 1;\n```", "SELECT 1"),
            ("<think>plan it</think>SELECT 1", "SELECT 1"),
            ("<think>a\nb</think>\n```sql\nSELECT 1\n```", "SELECT 1"),
            ("Here you go:\n```sql\nSELECT 1\n```\nHope it helps", "SELECT 1"),
        ],
    )
    def test_cleaning(self, raw, expected):
        assert clean_sql(raw) == expected


class TestHistory:

    messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "sql": "SELECT 1", "content": "ok"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "clarify": True, "content": "which year?"},
        {"role": "user", "content": "2024"},
        {"role": "assistant", "error": "boom"},
    ]

    def test_turns_include_sql_and_clarifications_only(self):
        assert build_history(self.messages, 10) == [
            ("human", "q1"),
            ("ai", "SELECT 1"),
            ("human", "q2"),
            ("ai", f"{CLARIFY_PREFIX} which year?"),
            ("human", "2024"),
        ]

    def test_only_recent_turns_are_kept(self):
        assert build_history(self.messages, 1) == [
            ("ai", f"{CLARIFY_PREFIX} which year?"),
            ("human", "2024"),
        ]

    def test_empty(self):
        assert build_history([], 3) == []


@pytest.fixture
def llm(scripted_llm):
    def make(*answers):
        queue = list(answers)
        return scripted_llm(lambda prompt, n: queue.pop(0))
    return make


class TestGeneration:

    def test_returns_sql_operation_and_tokens(self, llm):
        response = process_question(llm("```sql\nSELECT 1;\n```"), "q", "ctx")

        assert response.sql == "SELECT 1"
        assert response.operation == "READ"
        assert (response.input_tokens, response.output_tokens) == (100, 10)

    def test_write_statements_are_classified(self, llm):
        response = process_question(llm("DELETE FROM t"), "q", "ctx")

        assert response.operation == "DML"

    def test_dialect_changes_prompt_and_classification(self, llm):
        model = llm("SELECT TOP 5 * FROM t")
        response = process_question(model, "q", "ctx", dialect_key="mssql")

        assert response.operation == "READ"
        assert "SQL Server" in model.calls[0]
        assert "TOP" in model.calls[0]

    def test_prompt_contains_context_examples_glossary_and_question(self, llm):
        model = llm("SELECT 1")

        process_question(
            model, "how many cats", "CTX-MARK", [("human", "earlier"), ("ai", "SELECT 0")],
            examples="EXAMPLE-MARK", glossary="GLOSS-MARK",
        )
        prompt = model.calls[0]

        for marker in ("CTX-MARK", "EXAMPLE-MARK", "GLOSS-MARK", "how many cats", "earlier", "SELECT 0"):
            assert marker in prompt

    def test_clarifying_question_raises(self, llm):
        with pytest.raises(NeedsClarification, match="which year"):
            process_question(llm("CLARIFY: which year?"), "q", "ctx")

    def test_clarify_prefix_is_case_insensitive(self, llm):
        with pytest.raises(NeedsClarification):
            process_question(llm("clarify: which year?"), "q", "ctx")

    def test_cannot_generate_raises(self, llm):
        with pytest.raises(NoSQLGenerated):
            process_question(llm("CANNOT_GENERATE_SQL"), "q", "ctx")

    def test_empty_answer_raises(self, llm):
        with pytest.raises(ValueError, match="empty"):
            process_question(llm("   "), "q", "ctx")

    def test_list_content_blocks_are_flattened(self, scripted_llm):
        model = scripted_llm(
            lambda p, n: [{"type": "text", "text": "SELECT "}, "1", {"type": "tool_use"}]
        )

        assert process_question(model, "q", "ctx").sql == "SELECT 1"

    def test_missing_usage_metadata_counts_as_zero(self):
        class Bare:
            def invoke(self, messages):
                return type("R", (), {"content": "SELECT 1"})()

        response = process_question(Bare(), "q", "ctx")

        assert (response.input_tokens, response.output_tokens) == (0, 0)

    def test_repair_sends_failed_sql_and_error(self, llm):
        model = llm("SELECT 2")

        response = repair_question(
            model, "orig question", "ctx", [], "SELECT bad", "column bad does not exist"
        )
        prompt = model.calls[0]

        assert response.sql == "SELECT 2"
        assert "orig question" in prompt
        assert "SELECT bad" in prompt
        assert "column bad does not exist" in prompt


class TestSummary:

    def test_summary_prompt_and_cleanup(self, llm):
        model = llm("<think>hm</think>Two customers spent money.")
        frame = pd.DataFrame({"name": ["Ann", "Bob"], "spent": [170.0, 300.0]})

        text, tokens_in, tokens_out = summarize_result(model, "who spent?", frame)

        assert text == "Two customers spent money."
        assert (tokens_in, tokens_out) == (100, 10)
        assert "who spent?" in model.calls[0]
        assert "Ann" in model.calls[0]
        assert "2 rows total" in model.calls[0]

    def test_only_a_sample_of_rows_and_short_cells_are_sent(self, llm):
        model = llm("ok")
        frame = pd.DataFrame({"v": ["x" * 500] * 100})

        summarize_result(model, "q", frame)

        assert "100 rows total" in model.calls[0]
        assert model.calls[0].count("x" * 60) == 20
        assert "x" * 61 not in model.calls[0]
