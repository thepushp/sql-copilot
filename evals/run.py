"""Score the SQL the app generates against reference queries.

    python -m evals.run --datasource Shop --cases evals/cases.example.json \
        --provider Ollama --model llama3.2 [--no-smart-select] [--json report.json]

Runs the same pipeline as the app (retrieval, table choice, prompt) without
Streamlit, so a change to prompts, retrieval or model can be compared by score.
Cases are JSON: [{"id", "question", "sql", "ordered"?, "tags"?}].
"""

import argparse
import json
import sys
from dataclasses import asdict

from config.datasource_config import DATASOURCES
from config.model_config import CONTEXT_PROFILES
from config.privacy import PrivacySettings, apply_privacy
from database.database_inspector import inspect_database
from database.dialects import get_dialect
from database.query_executor import execute_query
from database.sql_guard import validate_sql
from rag.context_builder import build_context
from rag.embeddings import get_embeddings
from rag.retriever import add_related_tables, retrieve_candidates, retrieve_documents
from rag.schema_indexer import build_schema_index
from services.evaluation import format_report, load_cases, run_suite, summarize
from services.glossary import expand_query, load_glossary
from services.llm_service import get_llm
from services.query_service import process_question
from services.table_selector import select_tables


def build_pipeline(datasource, config, llm, profile, smart_select=True, embedding_model=None):
    """Returns (generate, execute, allowed) wired like the app does it."""

    dialect_key = get_dialect(config).key

    metadata, policy = apply_privacy(inspect_database(datasource, config), PrivacySettings())

    index = build_schema_index(metadata.to_documents(), get_embeddings(embedding_model))

    glossary = load_glossary()

    def generate(question):

        search = expand_query(question, glossary)

        if smart_select:

            candidates = retrieve_candidates(
                index, search, limit=profile["shortlist"], fetch_k=profile["fetch_k"]
            )

            try:
                chosen, _, _ = select_tables(
                    llm, question, candidates, glossary=glossary, max_tables=profile["k"]
                )
            except Exception:
                chosen = []

            documents = add_related_tables(
                index, chosen or candidates[: profile["k"]], profile["max_neighbors"]
            )

        else:

            documents = retrieve_documents(index, search, profile)

        return process_question(
            llm=llm,
            question=question,
            context=build_context(documents, profile),
            glossary=glossary,
            dialect_key=dialect_key,
        )

    def execute(sql):
        return execute_query(datasource, sql, config, max_rows=10_000)

    def allowed(sql):
        return validate_sql(sql, policy, dialect_key).allowed

    return generate, execute, allowed


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datasource", required=True, help="name from .env (DEV/TEST)")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--profile", default="Standard", choices=list(CONTEXT_PROFILES))
    parser.add_argument("--no-smart-select", action="store_true")
    parser.add_argument("--json", help="also write the full results here")
    args = parser.parse_args(argv)

    config = DATASOURCES[args.datasource]

    llm = get_llm(provider=args.provider, model=args.model, temperature=0.0)

    generate, execute, allowed = build_pipeline(
        args.datasource,
        config,
        llm,
        CONTEXT_PROFILES[args.profile],
        smart_select=not args.no_smart_select,
    )

    outcomes = run_suite(
        load_cases(args.cases),
        generate,
        execute,
        allowed,
        progress=lambda o: print(f"{o.status:15} {o.case.id}", file=sys.stderr),
    )

    print(format_report(outcomes))

    if args.json:

        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                {"summary": summarize(outcomes), "results": [asdict(o) for o in outcomes]},
                handle,
                indent=2,
                default=str,
            )

    return 0 if all(o.status == "pass" for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
