"""Query history for the current session.

History is derived from the chat messages (question + SQL only). Pins are kept
in the session and hold only question + SQL, so they survive "Clear chat".
Nothing here reads or stores result rows, and nothing is written to disk
unless HISTORY_PERSIST is set (question + SQL only, in git-ignored data/,
overridable with HISTORY_PATH).
"""

import csv
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path


HISTORY_PATH = Path(
    os.getenv(
        "HISTORY_PATH",
        Path(__file__).resolve().parent.parent
        / "data"
        / "query_history.json",
    )
)

PERSIST = os.getenv("HISTORY_PERSIST", "").lower() in ("1", "true", "yes")


@dataclass
class HistoryItem:
    datasource: str
    question: str
    sql: str
    pinned: bool = False


def _key(datasource, question, sql):

    return (datasource, question.strip(), (sql or "").strip())


def _pin_key(pin):

    return _key(pin["datasource"], pin["question"], pin.get("sql", ""))


def collect(messages, datasource):
    """Questions asked in this session for a datasource, newest first, no duplicates."""

    items = []
    seen = set()

    for message in reversed(messages):

        if message.get("role") != "assistant":
            continue

        if message.get("datasource") != datasource:
            continue

        question = (message.get("question") or "").strip()

        if not question:
            continue

        sql = (message.get("sql") or "").strip()

        key = _key(datasource, question, sql)

        if key in seen:
            continue

        seen.add(key)

        items.append(HistoryItem(datasource, question, sql))

    return items


def merge(items, pins, datasource):
    """Pinned entries first (including ones no longer in the chat), then the rest."""

    pinned_keys = {_pin_key(pin) for pin in pins}

    by_key = {
        _key(item.datasource, item.question, item.sql): item
        for item in items
    }

    result = []

    for pin in pins:

        if pin["datasource"] != datasource:
            continue

        by_key.pop(_pin_key(pin), None)

        result.append(
            HistoryItem(
                datasource,
                pin["question"],
                pin.get("sql", ""),
                True,
            ),
        )

    for key, item in by_key.items():

        item.pinned = key in pinned_keys

        result.append(item)

    return result


def toggle_pin(pins, item):
    """Return a new pins list with the item pinned or unpinned."""

    key = _key(item.datasource, item.question, item.sql)

    kept = [pin for pin in pins if _pin_key(pin) != key]

    if len(kept) != len(pins):
        return kept

    return kept + [
        {
            "datasource": item.datasource,
            "question": item.question.strip(),
            "sql": item.sql.strip(),
        },
    ]


def to_json(items):

    return json.dumps(
        [
            {
                "datasource": item.datasource,
                "question": item.question,
                "sql": item.sql,
                "pinned": item.pinned,
            }
            for item in items
        ],
        indent=2,
    )


def to_csv(items):

    buffer = io.StringIO()

    writer = csv.writer(buffer)

    writer.writerow(["datasource", "question", "sql", "pinned"])

    for item in items:

        writer.writerow(
            [item.datasource, item.question, item.sql, item.pinned],
        )

    return buffer.getvalue()


def load_pins():
    """Pins saved on disk (only when HISTORY_PERSIST is set)."""

    if not PERSIST or not HISTORY_PATH.exists():
        return []

    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    return [
        {
            "datasource": str(entry["datasource"]),
            "question": str(entry["question"]),
            "sql": str(entry.get("sql", "")),
        }
        for entry in data
        if isinstance(entry, dict)
        and "datasource" in entry
        and "question" in entry
    ]


def save_pins(pins):

    if not PERSIST:
        return

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary = HISTORY_PATH.with_suffix(".tmp")

    temporary.write_text(json.dumps(pins, indent=2), encoding="utf-8")

    temporary.replace(HISTORY_PATH)
