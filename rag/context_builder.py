def build_context(documents) -> str:

    sections = []

    for document in documents:

        sections.append(
            document.page_content
        )

    return "\n\n---\n\n".join(sections)