import json
import re

from prompts.sql_prompt import TABLE_SELECT_PROMPT


def _content_to_text(content):

    if isinstance(content, str):
        return content

    return "".join(
        block if isinstance(block, str) else block.get("text", "")
        for block in content
        if isinstance(block, (str, dict))
    )


def _parse_keys(text, valid):

    lookup = {key.lower(): key for key in valid}

    text = re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE
    )

    chosen = []

    match = re.search(r"\[.*?\]", text, flags=re.DOTALL)

    if match:

        try:
            items = json.loads(match.group(0))
        except json.JSONDecodeError:
            items = []

        for item in items:

            key = lookup.get(str(item).strip().lower())

            if key and key not in chosen:
                chosen.append(key)

    if not chosen:

        for key in valid:

            if key.lower() in text.lower() and key not in chosen:
                chosen.append(key)

    return chosen


def select_tables(
    llm,
    question,
    candidates,
    history_text="",
    glossary="",
    max_tables=8,
):
    """Ask the model which shortlisted tables are needed.

    Returns (documents, input_tokens, output_tokens).
    """

    by_key = {doc.metadata["key"]: doc for doc in candidates}

    messages = TABLE_SELECT_PROMPT.invoke(
        {
            "question": question,
            "tables": "\n".join(
                doc.metadata.get("summary", doc.metadata["key"])
                for doc in candidates
            ),
            "history": history_text or "None",
            "glossary": glossary.strip() or "None",
            "max_tables": max_tables,
        }
    )

    response = llm.invoke(messages)

    keys = _parse_keys(_content_to_text(response.content), list(by_key))

    usage = getattr(response, "usage_metadata", None) or {}

    return (
        [by_key[key] for key in keys[:max_tables]],
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
    )
