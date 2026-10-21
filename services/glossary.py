import os
import re
from pathlib import Path

from rag.textutil import overlap, words


GLOSSARY_PATH = Path(
    os.getenv(
        "GLOSSARY_PATH",
        Path(__file__).resolve().parent.parent / "data" / "glossary.txt",
    )
)

MAX_GLOSSARY_CHARS = 3000

_SPLIT = re.compile(r"\s*(?::|=|->|means)\s*", re.IGNORECASE)


def load_glossary() -> str:

    try:
        return GLOSSARY_PATH.read_text(encoding="utf-8")[:MAX_GLOSSARY_CHARS]
    except OSError:
        return ""


def save_glossary(text: str) -> None:

    GLOSSARY_PATH.parent.mkdir(parents=True, exist_ok=True)

    GLOSSARY_PATH.write_text(
        text.strip()[:MAX_GLOSSARY_CHARS] + "\n",
        encoding="utf-8",
    )


def expand_query(question: str, glossary: str) -> str:
    """Append the meaning of any glossary term found in the question."""

    if not glossary.strip():
        return question

    question_words = words(question)

    additions = []

    for line in glossary.splitlines():

        parts = _SPLIT.split(line.strip(), maxsplit=1)

        if len(parts) != 2 or not parts[1]:
            continue

        term_words = words(parts[0])

        if term_words and overlap(term_words, question_words) == len(
            term_words
        ):
            additions.append(parts[1])

    return question + (" " + " ".join(additions) if additions else "")
