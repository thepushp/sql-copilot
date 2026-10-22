import json
from types import SimpleNamespace

import pandas as pd
import pytest

from database.query_executor import execute_query
from database.sql_guard import validate_sql
from services import evaluation as ev
from services.query_service import NeedsClarification, NoSQLGenerated


def frame(**columns):
    return pd.DataFrame(columns)


class TestResultsMatch:

    def test_ignores_column_names_column_order_and_row_order(self):
        a = frame(x=[1, 2], y=["a", "b"])
        b = frame(label=["b", "a"], n=[2, 1])
        assert ev.results_match(a, b)[0]

    def test_numbers_compare_by_value(self):
        assert ev.results_match(frame(x=[1]), frame(y=[1.0]))[0]
        assert ev.results_match(frame(x=["2.50"]), frame(y=[2.5]))[0]

    def test_nulls_match_each_other(self):
        assert ev.results_match(frame(x=[None, 1]), frame(y=[float("nan"), 1]))[0]

    def test_shape_and_value_differences(self):
        assert "column" in ev.results_match(frame(x=[1]), frame(x=[1], y=[2]))[1]
        assert "row" in ev.results_match(frame(x=[1]), frame(x=[1, 2]))[1]
        assert not ev.results_match(frame(x=[1, 2]), frame(x=[1, 3]))[0]

    def test_duplicates_matter(self):
        assert not ev.results_match(frame(x=[1, 1, 2]), frame(x=[1, 2, 2]))[0]

    def test_order_only_matters_when_asked(self):
        a, b = frame(x=[1, 2]), frame(x=[2, 1])
        assert ev.results_match(a, b)[0]
        assert not ev.results_match(a, b, ordered=True)[0]
        assert ev.results_match(a, a.copy(), ordered=True)[0]


@pytest.fixture
def pipeline(shop_config):
    def execute(sql):
        return execute_query("Shop", sql, shop_config)

    def allowed(sql):
        return validate_sql(sql, None, "sqlite").allowed

    return execute, allowed


def generator(answer):
    def generate(question):
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(sql=answer, input_tokens=10, output_tokens=2)
    return generate


CASE = ev.EvalCase("c1", "inactive?", "SELECT name FROM customer WHERE status = 'INACTIVE'")


class TestRunCase:

    @pytest.mark.parametrize(
        "answer, status",
        [
            ("SELECT name FROM customer WHERE status <> 'ACTIVE'", ev.PASS),
            ("SELECT name FROM customer", ev.WRONG),
            ("SELECT nope FROM customer", ev.EXEC_ERROR),
            ("DELETE FROM customer", ev.REJECTED),
            (NoSQLGenerated("no"), ev.NO_SQL),
            (NeedsClarification("which?"), ev.CLARIFY),
            (RuntimeError("model down"), ev.GEN_ERROR),
        ],
    )
    def test_statuses(self, pipeline, answer, status):
        outcome = ev.run_case(CASE, generator(answer), *pipeline)
        assert outcome.status == status

    def test_broken_reference_is_reported_not_scored(self, pipeline):
        case = ev.EvalCase("bad", "q", "SELECT nope FROM customer")
        assert ev.run_case(case, generator("SELECT 1"), *pipeline).status == ev.REFERENCE_ERROR


class TestSuite:

    def test_summary_and_report(self, pipeline):
        cases = [CASE, ev.EvalCase("c2", "all", "SELECT name FROM customer")]
        outcomes = ev.run_suite(cases, generator("SELECT name FROM customer"), *pipeline)

        summary = ev.summarize(outcomes)

        assert summary["total"] == 2 and summary["passed"] == 1
        assert summary["accuracy"] == 0.5
        assert summary["input_tokens"] == 20
        report = ev.format_report(outcomes)
        assert "1/2 correct (50%)" in report and "FAIL c1" in report and "wrong_result" in report

    def test_empty_suite(self):
        assert ev.summarize([])["accuracy"] == 0.0


class TestCaseFile:

    def test_load_and_defaults(self, tmp_path):
        path = tmp_path / "cases.json"
        path.write_text(json.dumps([{"question": "q", "sql": "SELECT 1", "ordered": True}]))

        (case,) = ev.load_cases(path)

        assert case.id == "case-1" and case.ordered and case.tags == []

    def test_the_example_file_is_valid_against_the_test_database(self, pipeline):
        execute, _ = pipeline
        for case in ev.load_cases("evals/cases.example.json"):
            assert not execute(case.sql).error, case.id


class TestCommandLinePipeline:

    def test_pipeline_scores_a_scripted_model(self, env, shop_config, scripted_llm):
        import json as _json

        from config.model_config import CONTEXT_PROFILES
        from evals.run import build_pipeline

        def respond(prompt, number):
            if "choose which database tables" in prompt:
                return _json.dumps(["main.customer"])
            return "SELECT name FROM customer WHERE status = 'INACTIVE'"

        generate, execute, allowed = build_pipeline(
            "Shop", shop_config, scripted_llm(respond), CONTEXT_PROFILES["Compact"]
        )

        outcomes = ev.run_suite(ev.load_cases("evals/cases.example.json")[:1], generate, execute, allowed)

        assert [o.status for o in outcomes] == [ev.PASS]
