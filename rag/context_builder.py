def _full_text(document):

    return document.metadata.get("content", document.page_content)


def _strip_indexes(content):

    head, _, _ = content.partition("\nIndexes:")

    return head


def build_context(documents, profile=None) -> str:

    profile = profile or {}

    max_chars = profile.get("max_chars")
    include_indexes = profile.get("include_indexes", True)

    sections = []
    used = 0

    for document in documents:

        text = _full_text(document)

        if not include_indexes:
            text = _strip_indexes(text)

        if max_chars and sections and used + len(text) > max_chars:
            break

        sections.append(text)
        used += len(text)

    return "\n\n---\n\n".join(sections)
