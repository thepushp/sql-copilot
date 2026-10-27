import json
import os
from pathlib import Path

import numpy as np


STORE_PATH = Path(
    os.getenv(
        "EXAMPLES_PATH",
        Path(__file__).resolve().parent.parent
        / "data"
        / "approved_queries.json",
    )
)

MIN_SIMILARITY = 0.55

_embedding_cache: dict[tuple[str, str], list[float]] = {}


def load_examples() -> list[dict]:

    if not STORE_PATH.exists():
        return []

    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def add_example(datasource: str, question: str, sql: str) -> bool:

    examples = load_examples()

    entry = {
        "datasource": datasource,
        "question": question.strip(),
        "sql": sql.strip(),
    }

    if entry in examples:
        return False

    examples.append(entry)

    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary = STORE_PATH.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(examples, indent=2),
        encoding="utf-8",
    )

    temporary.replace(STORE_PATH)

    return True


def remove_example(datasource: str, question: str, sql: str) -> bool:

    entry = {
        "datasource": datasource,
        "question": question.strip(),
        "sql": sql.strip(),
    }

    examples = load_examples()

    if entry not in examples:
        return False

    examples = [example for example in examples if example != entry]

    temporary = STORE_PATH.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(examples, indent=2),
        encoding="utf-8",
    )

    temporary.replace(STORE_PATH)

    return True


def _embed(embeddings, model_name, texts):

    missing = [
        text
        for text in texts
        if (model_name, text) not in _embedding_cache
    ]

    if missing:

        for text, vector in zip(
            missing,
            embeddings.embed_documents(missing),
        ):

            _embedding_cache[(model_name, text)] = vector

    return np.array(
        [_embedding_cache[(model_name, text)] for text in texts]
    )


def find_similar(
    embeddings,
    model_name: str,
    datasource: str,
    question: str,
    limit: int = 3,
) -> list[dict]:

    candidates = [
        example
        for example in load_examples()
        if example["datasource"] == datasource
    ]

    if not candidates or limit <= 0:
        return []

    matrix = _embed(
        embeddings,
        model_name,
        [example["question"] for example in candidates],
    )

    query = np.array(embeddings.embed_query(question))

    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)

    scores = matrix @ query / np.where(norms == 0, 1, norms)

    ranked = sorted(
        zip(scores, candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )

    return [
        example
        for score, example in ranked[:limit]
        if score >= MIN_SIMILARITY
    ]


def format_examples(examples: list[dict]) -> str:

    if not examples:
        return "None"

    return "\n\n".join(
        f"Question: {example['question']}\nSQL: {example['sql']}"
        for example in examples
    )
