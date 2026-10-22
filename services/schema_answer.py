"""Answer questions about the schema itself ("which table stores X") without a model."""

import re

from services.relationship_answer import answer_relationship_question
from services.schema_search import search_metadata


_LOOKUP = re.compile(
    r"\b(?:which|what)\s+(?:tables?|columns?)\s+(?:\w+\s+){0,2}?"
    r"(?:stores?|contains?|has|have|holds?|keeps?|includes?|records?)\s+(?P<a>.+)"
    r"|\bwhere\s+(?:is|are|can\s+i\s+find|do\s+(?:we|i)\s+(?:store|find|keep))\s+(?P<b>.+)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the", "a", "an", "of", "all", "any", "data", "information", "info",
    "details", "stored", "store", "found", "in", "this", "database", "table",
    "tables", "my", "our", "is", "are", "for", "to", "and", "me",
}

MAX_HITS = 8
MAX_COLUMNS = 4


def _terms(question):

    match = _LOOKUP.search(question.strip().rstrip("?. "))

    if not match:
        return []

    phrase = match.group("a") or match.group("b") or ""

    return [
        word
        for word in re.findall(r"[a-z0-9_]+", phrase.lower())
        if word not in _STOPWORDS
    ]


def _search(metadata, terms):

    hits = search_metadata(metadata, " ".join(terms), limit=MAX_HITS)

    if hits or len(terms) < 2:
        return hits

    seen = {}

    for term in terms:
        for hit in search_metadata(metadata, term, limit=MAX_HITS):
            seen.setdefault(hit.key, hit)

    return sorted(seen.values(), key=lambda hit: (-hit.score, hit.key))[:MAX_HITS]


def _table(metadata, key):

    for schema in metadata.schemas:
        for table in schema.tables:
            if f"{table.schema}.{table.name}" == key:
                return table

    return None


def _lookup_answer(metadata, terms):

    hits = _search(metadata, terms)

    if not hits:
        return (
            f"No table or column matches **{' '.join(terms)}**. "
            "Try a different word, or use the Explorer search."
        )

    lines = [f"Tables that match **{' '.join(terms)}**, best match first:"]

    for hit in hits:

        table = _table(metadata, hit.key)

        columns = [
            column.name
            for column in table.columns
            if any(term.rstrip("s") in column.name.lower() for term in terms)
        ][:MAX_COLUMNS]

        rows = (
            f", about {table.approx_rows:,} rows"
            if table.approx_rows is not None
            else ""
        )

        detail = f" - columns: {', '.join(f'`{name}`' for name in columns)}" if columns else ""

        lines.append(f"- `{hit.key}` ({table.kind}{rows}){detail}")

    lines.append(
        "\nTables with 0 rows or a prefix such as `trf_` may be copies or "
        "staging tables; check the row count before relying on one."
    )

    return "\n".join(lines)


def answer_schema_question(question, metadata):
    """Markdown answer for schema questions, or None to fall through to SQL."""

    if metadata is None:
        return None

    relationships = answer_relationship_question(question, metadata)

    if relationships:
        return relationships

    terms = _terms(question)

    if not terms:
        return None

    return _lookup_answer(metadata, terms)
