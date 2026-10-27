"""Answers the user marked wrong, so similar questions can steer clear of them.

Only the question, the SQL and the reason are stored (never any data). Like the
approved examples, the file belongs to the server, not to a user.
"""

import json
import os
from pathlib import Path

from services.example_store import rank_similar


STORE_PATH = Path(
    os.getenv(
        "REJECTED_PATH",
        Path(__file__).resolve().parent.parent / "data" / "rejected_queries.json",
    )
)

MAX_KEPT = 200


def load_rejections() -> list[dict]:

    if not STORE_PATH.exists():
        return []

    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(entries):

    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary = STORE_PATH.with_suffix(".tmp")

    temporary.write_text(json.dumps(entries[-MAX_KEPT:], indent=2), encoding="utf-8")

    temporary.replace(STORE_PATH)


def _same(entry, datasource, question, sql):

    return (
        entry["datasource"] == datasource
        and entry["question"] == question.strip()
        and entry["sql"] == sql.strip()
    )


def add_rejection(datasource, question, sql, reason, note=""):

    entries = [
        entry
        for entry in load_rejections()
        if not _same(entry, datasource, question, sql)
    ]

    entries.append(
        {
            "datasource": datasource,
            "question": question.strip(),
            "sql": sql.strip(),
            "reason": reason,
            "note": (note or "").strip(),
        }
    )

    _save(entries)


def remove_rejection(datasource, question, sql) -> bool:

    entries = load_rejections()

    kept = [entry for entry in entries if not _same(entry, datasource, question, sql)]

    if len(kept) == len(entries):
        return False

    _save(kept)

    return True


def find_similar_rejections(embeddings, model_name, datasource, question, limit=2):

    candidates = [
        entry for entry in load_rejections() if entry["datasource"] == datasource
    ]

    return rank_similar(embeddings, model_name, candidates, question, limit)


def format_rejections(entries) -> str:

    if not entries:
        return "None"

    blocks = []

    for entry in entries:

        why = entry["reason"] + (f" - {entry['note']}" if entry.get("note") else "")

        blocks.append(
            f"Question: {entry['question']}\nWrong SQL: {entry['sql']}\nWhy: {why}"
        )

    return "\n\n".join(blocks)
