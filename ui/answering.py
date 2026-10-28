"""Turns a question into an answer: retrieval, SQL, execution, repair and checks."""

import time

from sqlalchemy import text
import streamlit as st

from database.query_executor import execute_query
from database.sql_guard import validate_sql
from rag.context_builder import build_context
from rag.retriever import (
    add_related_tables,
    documents_for_keys,
    retrieve_candidates,
    retrieve_documents,
)
from services.audit import audit
from services.glossary import expand_query, load_glossary
from services.table_selector import select_tables
from services.relationship_answer import related_tables
from services.schema_answer import answer_schema_question
from services.query_checks import (
    estimate_plan,
    find_stored_values,
    plan_label,
    plan_warnings,
    review,
)
from services.sql_explain import describe_sql
from services.sql_qualify import finalize_sql, tables_in_sql
from services.example_store import find_similar, format_examples
from services.feedback_store import find_similar_rejections, format_rejections
from services.query_service import (
    NeedsClarification,
    NoSQLGenerated,
    process_question,
    refine_question,
    repair_question,
    summarize_result,
)
from services import llm_service
from rag import embeddings
from ui.state import logger


def annotate(message, config):
    """Fill in the plain-English plan, plan estimate and plausibility warnings."""

    dialect_key = message.get("dialect", "postgresql")

    message["plan"] = describe_sql(message["sql"], dialect_key)

    rows = message.get("rows")

    issues = []
    estimate = None

    try:

        issues = review(
            message["sql"],
            rows,
            dialect_key,
            st.session_state.database_metadata,
        )

        if rows is not None and not message.get("example"):

            estimate = estimate_plan(
                message["datasource"],
                message["sql"],
                config,
                dialect_key,
            )

    except Exception:

        logger.warning("Query checks failed", exc_info=True)

    message["issues"] = issues
    message["warnings"] = [issue.text for issue in issues] + plan_warnings(estimate)
    message["estimate"] = plan_label(estimate)


def _history_text(history):

    return "\n".join(
        f"{'User' if role == 'human' else 'Assistant'}: {text[:300]}"
        for role, text in history[-4:]
    )


def _answer_question(question, history, settings, status):

    message = {
        "role": "assistant",
        "question": question,
        "datasource": settings.datasource_name,
        "dialect": settings.dialect_key,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    def add_usage(response_or_tuple):

        if isinstance(response_or_tuple, tuple):

            _, tokens_in, tokens_out = response_or_tuple

        else:

            tokens_in = response_or_tuple.input_tokens
            tokens_out = response_or_tuple.output_tokens

        message["input_tokens"] += tokens_in
        message["output_tokens"] += tokens_out

    profile = settings.profile
    policy = st.session_state.access_policy

    try:

        relationships = answer_schema_question(
            question,
            st.session_state.database_metadata,
        )

        if relationships:

            message["content"] = relationships

            tables = related_tables(
                question,
                st.session_state.database_metadata,
            )

            if tables is not None:
                message["er_tables"] = tables[:3]

            return message

        glossary = load_glossary()

        search_query = expand_query(question, glossary)

        llm = llm_service.get_llm(
            provider=settings.provider,
            model=settings.model,
            temperature=settings.temperature,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )

        index = st.session_state.schema_index

        documents = None

        if settings.smart_select:

            status.update(label="Choosing relevant tables...")

            candidates = retrieve_candidates(
                index,
                search_query,
                limit=profile["shortlist"],
                fetch_k=profile["fetch_k"],
            )

            try:

                chosen, tokens_in, tokens_out = select_tables(
                    llm,
                    question,
                    candidates,
                    history_text=_history_text(history),
                    glossary=glossary,
                    max_tables=profile["k"],
                )

                message["input_tokens"] += tokens_in
                message["output_tokens"] += tokens_out

                if chosen:

                    documents = add_related_tables(
                        index,
                        chosen,
                        profile["max_neighbors"],
                    )

            except Exception:

                logger.warning(
                    "Table selection failed; using ranked tables",
                    exc_info=True,
                )

                documents = None

            if documents is None:

                documents = add_related_tables(
                    index,
                    candidates[: profile["k"]],
                    profile["max_neighbors"],
                )

        else:

            status.update(label="Finding relevant tables...")

            documents = retrieve_documents(index, search_query, profile)

        metadata = st.session_state.database_metadata

        previous_sql = next(
            (
                item["sql"]
                for item in reversed(st.session_state.messages)
                if item["role"] == "assistant" and item.get("sql")
            ),
            "",
        )

        if history and previous_sql:

            present = {
                document.metadata.get("key")
                for document in documents
            }

            documents = [
                document
                for document in documents_for_keys(
                    index,
                    tables_in_sql(previous_sql, metadata, settings.dialect_key),
                )
                if document.metadata.get("key") not in present
            ] + documents

        context = build_context(documents, profile)

        message["tables"] = [
            document.metadata["key"]
            for document in documents
            if "key" in document.metadata
        ]

        try:

            found = find_similar(
                embeddings.get_embeddings(st.session_state.index_embedding_model),
                st.session_state.index_embedding_model,
                settings.datasource_name,
                question,
                profile["examples"],
            )

        except Exception:

            logger.warning("Approved-example lookup failed", exc_info=True)

            found = []

        examples = format_examples(found)

        message["examples_used"] = len(found)

        try:

            rejected = find_similar_rejections(
                embeddings.get_embeddings(st.session_state.index_embedding_model),
                st.session_state.index_embedding_model,
                settings.datasource_name,
                question,
            )

        except Exception:

            logger.warning("Rejected-answer lookup failed", exc_info=True)

            rejected = []

        message["rejections_used"] = len(rejected)

        status.update(label="Generating SQL...")

        response = process_question(
            llm=llm,
            question=question,
            context=context,
            history=history,
            examples=examples,
            glossary=glossary,
            dialect_key=settings.dialect_key,
            previous_sql=previous_sql if history else "",
            on_token=getattr(status, "stream", None),
            rejected=format_rejections(rejected) if rejected else "",
        )

        add_usage(response)

        response.sql = finalize_sql(response.sql, metadata, settings.dialect_key)

        message["sql"] = response.sql

        message["plan"] = describe_sql(response.sql, settings.dialect_key)

        if response.operation != "READ":

            message["content"] = (
                f"Generated **{response.operation}** SQL for review."
            )

            message["blocked"] = True

            return message

        validation = validate_sql(
            response.sql,
            policy,
            settings.dialect_key,
        )

        if not validation.allowed:

            message["error"] = (
                "The generated SQL was rejected by the "
                f"safety guard: {validation.reason}"
            )
            message["error_kind"] = "guard"

            return message

        status.update(label="Running query...")

        result = execute_query(
            settings.datasource_name,
            response.sql,
            settings.datasource_config,
        )

        if result.error:

            status.update(label="Query failed, retrying...")

            try:

                repaired = repair_question(
                    llm=llm,
                    question=question,
                    context=context,
                    history=history,
                    failed_sql=response.sql,
                    error=result.error,
                    examples=examples,
                    glossary=glossary,
                    dialect_key=settings.dialect_key,
                    on_token=getattr(status, "stream", None),
                )

                add_usage(repaired)

                repaired.sql = finalize_sql(repaired.sql, metadata, settings.dialect_key)

                if validate_sql(
                    repaired.sql,
                    policy,
                    settings.dialect_key,
                ).allowed:

                    retry = execute_query(
                        settings.datasource_name,
                        repaired.sql,
                        settings.datasource_config,
                    )

                    if not retry.error:

                        result = retry
                        message["sql"] = repaired.sql
                        message["corrected"] = True

            except Exception:

                logger.warning("Automatic repair failed", exc_info=True)

        if result.error:

            message["error"] = f"The query failed: {result.error}"
            message["error_kind"] = "query"

            return message

        if result.rows.empty:

            fixable = [
                issue
                for issue in review(
                    message["sql"],
                    result.rows,
                    settings.dialect_key,
                    st.session_state.database_metadata,
                )
                if issue.fixable
            ]

            problems = [issue.text for issue in fixable]

            if fixable:

                try:

                    problems += find_stored_values(
                        fixable,
                        settings.datasource_name,
                        settings.datasource_config,
                        settings.dialect_key,
                        lambda sql: validate_sql(
                            sql,
                            policy,
                            settings.dialect_key,
                        ).allowed,
                    )

                except Exception:

                    logger.warning("Stored-value lookup failed", exc_info=True)

            if problems:

                status.update(label="No rows, checking filter values...")

                try:

                    refined = refine_question(
                        llm=llm,
                        question=question,
                        context=context,
                        history=history,
                        sql=message["sql"],
                        problems=problems,
                        examples=examples,
                        glossary=glossary,
                        dialect_key=settings.dialect_key,
                        on_token=getattr(status, "stream", None),
                    )

                    add_usage(refined)

                    refined.sql = finalize_sql(refined.sql, metadata, settings.dialect_key)

                    if validate_sql(
                        refined.sql,
                        policy,
                        settings.dialect_key,
                    ).allowed:

                        retry = execute_query(
                            settings.datasource_name,
                            refined.sql,
                            settings.datasource_config,
                        )

                        if not retry.error and not retry.rows.empty:

                            result = retry
                            message["sql"] = refined.sql
                            message["corrected"] = True

                except Exception:

                    logger.warning("Empty-result retry failed", exc_info=True)

        message["rows"] = result.rows
        message["row_count"] = result.row_count
        message["content"] = (
            f"**{result.row_count} rows** in {result.execution_ms:.0f} ms"
        )

        annotate(message, settings.datasource_config)

        if settings.summarize and not result.rows.empty:

            status.update(label="Summarizing...")

            try:

                summary = summarize_result(llm, question, result.rows)

                message["summary"] = summary[0]

                add_usage(summary)

            except Exception:

                logger.warning("Result summary failed", exc_info=True)

        return message

    except NeedsClarification as exc:

        message["clarify"] = True
        message["content"] = str(exc)

        return message

    except NoSQLGenerated as exc:

        message["content"] = str(exc)
        message["no_sql"] = True

        return message

    except Exception as exc:

        logger.warning("Answer failed: %s", exc, exc_info=True)

        message["error"] = str(exc)
        message["error_kind"] = "model"

        return message


def _outcome(reply):

    if reply.get("error"):

        return "rejected" if "safety guard" in reply["error"] else "error"

    if reply.get("clarify"):
        return "clarify"

    if reply.get("blocked"):
        return "blocked"

    if reply.get("rows") is not None:
        return "ok"

    return "no_sql"


def answer_question(question, history, settings, status):
    """Answer a question and record it in the audit trail."""

    started = time.perf_counter()

    reply = _answer_question(question, history, settings, status)

    reply["elapsed_ms"] = round((time.perf_counter() - started) * 1000)

    audit(
        "answer",
        datasource=settings.datasource_name,
        dialect=settings.dialect_key,
        provider=settings.provider,
        model=settings.model,
        question=question,
        sql=reply.get("sql"),
        outcome=_outcome(reply),
        rows=reply.get("row_count"),
        corrected=reply.get("corrected"),
        warnings=reply.get("warnings") or None,
        tables=reply.get("tables"),
        examples_used=reply.get("examples_used"),
        input_tokens=reply.get("input_tokens"),
        output_tokens=reply.get("output_tokens"),
        elapsed_ms=reply["elapsed_ms"],
        error=reply.get("error"),
    )

    return reply
