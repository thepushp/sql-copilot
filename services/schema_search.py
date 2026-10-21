"""Search tables, columns, comments and known values across the whole schema."""

from dataclasses import dataclass, field

from rag.textutil import matches, words


EXACT_TABLE = 100
PREFIX_TABLE = 70
PART_TABLE = 50
WORD_TABLE = 45
EXACT_COLUMN = 30
PART_COLUMN = 18
WORD_COLUMN = 12
COMMENT = 14
VALUE = 10

MAX_REASONS = 3


@dataclass
class SearchHit:
    key: str
    score: int
    reasons: list = field(default_factory=list)

    def summary(self):
        """Short text saying why this table matched."""

        return ", ".join(f"{kind}: {text}" for kind, text in self.reasons)


def _snippet(text, needle, width=34):

    position = text.lower().find(needle)

    if position < 0:
        return text[:width]

    start = max(0, position - width // 2)

    return ("…" if start else "") + text[start:start + width].strip() + "…"


def _score_word(word, table, key):
    """Best score and reason for one search word against one table."""

    name = table.name.lower()
    best = (0, None)

    def consider(score, reason):
        nonlocal best
        if score > best[0]:
            best = (score, reason)

    if word == name or word == key.lower():
        consider(EXACT_TABLE, ("table", table.name))
    elif name.startswith(word):
        consider(PREFIX_TABLE, ("table", table.name))
    elif word in name or word in key.lower():
        consider(PART_TABLE, ("table", table.name))
    elif any(matches(word, [part]) for part in words(name.replace("_", " "))):
        consider(WORD_TABLE, ("table", table.name))

    for column in table.columns:

        cname = column.name.lower()

        if word == cname:
            consider(EXACT_COLUMN, ("column", column.name))
        elif word in cname:
            consider(PART_COLUMN, ("column", column.name))
        elif any(matches(word, [part]) for part in words(cname.replace("_", " "))):
            consider(WORD_COLUMN, ("column", column.name))

        if column.comment and word in column.comment.lower():
            consider(COMMENT, ("comment", f"{column.name} — {_snippet(column.comment, word)}"))

        for value in column.values:
            if word in str(value).lower():
                consider(VALUE, ("value", f"{column.name} = {value}"))

    if table.comment and word in table.comment.lower():
        consider(COMMENT, ("comment", _snippet(table.comment, word)))

    return best


def search_metadata(metadata, query, limit=60):
    """Tables matching every word of the query, best first.

    Each word may match the table name, a column name, a comment or a known
    value. Plurals match ("students" finds "student").
    """

    terms = [t for t in query.lower().replace(".", " ").split() if t]

    if not terms and query.strip():
        terms = [query.strip().lower()]

    if not terms:
        return []

    hits = []

    for schema in metadata.schemas:

        for table in schema.tables:

            key = f"{table.schema}.{table.name}"
            total = 0
            reasons = []

            for term in terms:

                score, reason = _score_word(term, table, key)

                if score == 0:
                    total = 0
                    break

                total += score

                if reason not in reasons:
                    reasons.append(reason)

            if total:
                hits.append(SearchHit(key, total, reasons[:MAX_REASONS]))

    hits.sort(key=lambda hit: (-hit.score, hit.key))

    return hits[:limit]
