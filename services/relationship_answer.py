"""Answer "how are these tables connected" from the metadata, without a model."""

import re

from services.er_diagram import table_index
from services.table_info import incoming, outgoing


_RELATION_WORDS = re.compile(
    r"\b(connect\w*|relat\w*|link\w*|join\w*|foreign\s+keys?|references?|"
    r"referenc\w+|associat\w+|depend\w*|parents?|children|child)\b",
    re.IGNORECASE,
)

_DATA_WORDS = re.compile(
    r"\b(how\s+many|count|number\s+of|average|sum|total|list\s+all|show\s+me\s+"
    r"the\s+(rows|records|data))\b",
    re.IGNORECASE,
)

MAX_OVERVIEW_LINES = 60


def is_relationship_question(question):

    if not _RELATION_WORDS.search(question):
        return False

    return not _DATA_WORDS.search(question)


def _mentioned(question, index):

    text = question.lower()

    found = []

    for key, table in index.items():

        name = r"[_\s\-]".join(
            re.escape(part) for part in table.name.lower().split("_")
        )

        pattern = rf"(?<![a-z0-9_]){name}(?![a-z0-9_])"

        if re.search(pattern, text):
            found.append(key)

    return found


def _label(ref_inferred):

    return " (inferred)" if ref_inferred else ""


def _describe(key, index):

    table = index[key]

    lines = [f"**{key}**"]

    out = outgoing(table, index)
    inc = incoming(key, index)

    if out:

        lines.append("References (this table points to):")

        for ref in out:
            lines.append(
                f"- `{ref.column}` -> `{ref.other_key}.{ref.other_column}`"
                f"{_label(ref.inferred)}"
            )

    if inc:

        lines.append("Referenced by (these tables point here):")

        for ref in inc:
            lines.append(
                f"- `{ref.other_key}.{ref.other_column}` -> `{ref.column}`"
                f"{_label(ref.inferred)}"
            )

    if not out and not inc:
        lines.append("No known relationships.")

    return "\n".join(lines)


def _between(first, second, index):

    lines = []

    for ref in outgoing(index[first], index):
        if ref.other_key == second:
            lines.append(
                f"- `{first}.{ref.column}` -> `{second}.{ref.other_column}`"
                f"{_label(ref.inferred)}"
            )

    for ref in outgoing(index[second], index):
        if ref.other_key == first:
            lines.append(
                f"- `{second}.{ref.column}` -> `{first}.{ref.other_column}`"
                f"{_label(ref.inferred)}"
            )

    return lines


def _overview(index):

    lines = []

    for key, table in index.items():
        for ref in outgoing(table, index):
            lines.append(
                f"- `{key}.{ref.column}` -> `{ref.other_key}.{ref.other_column}`"
                f"{_label(ref.inferred)}"
            )

    if not lines:
        return "No relationships are known between the indexed tables."

    shown = lines[:MAX_OVERVIEW_LINES]

    extra = len(lines) - len(shown)

    if extra > 0:
        shown.append(f"- ... and {extra} more")

    return (
        "Relationships between the indexed tables "
        "(name a table to see just its connections):\n" + "\n".join(shown)
    )


def related_tables(question, metadata):
    """Tables named in a relationship question, or None if it is not one."""

    if metadata is None or not is_relationship_question(question):
        return None

    return _mentioned(question, table_index(metadata))


def answer_relationship_question(question, metadata):
    """Return markdown text, or None when the question is not about relationships."""

    if metadata is None or not is_relationship_question(question):
        return None

    index = table_index(metadata)

    keys = _mentioned(question, index)

    if not keys:
        return _overview(index)

    if len(keys) == 2:

        direct = _between(keys[0], keys[1], index)

        if direct:
            return (
                f"`{keys[0]}` and `{keys[1]}` are directly connected:\n"
                + "\n".join(direct)
            )

    return "\n\n".join(_describe(key, index) for key in keys[:4])
