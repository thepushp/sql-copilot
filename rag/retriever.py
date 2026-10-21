from rag.textutil import matches, overlap, words

TABLE_WEIGHT = 3.0
COLUMN_WEIGHT = 1.0
VALUE_WEIGHT = 1.5
MIN_KEYWORD_SCORE = 2.0
RRF_K = 30
DERIVED_PENALTY = 0.4

DERIVED_MARKERS = {
    "aggregated",
    "aggregate",
    "result",
    "log",
    "history",
    "audit",
    "template",
    "upload",
    "export",
    "backup",
    "archive",
    "tmp",
    "temp",
    "staging",
    "snapshot",
    "compiled",
    "historical",
    "mv",
    "v1",
    "v2",
}


def _name_words(name):

    return words(name.replace("_", " "))


def _keyword_score(question_words, document):

    metadata = document.metadata

    table_words = _name_words(metadata.get("key", "").split(".")[-1])

    column_words = set()

    for name in metadata.get("columns", []):
        column_words |= _name_words(name)

    value_words = set()

    for value in metadata.get("values", []):
        value_words |= _name_words(str(value))

    score = (
        TABLE_WEIGHT * overlap(question_words, table_words)
        + COLUMN_WEIGHT * overlap(question_words, column_words)
        + VALUE_WEIGHT * overlap(question_words, value_words)
    )

    if table_words and overlap(table_words, question_words) == len(
        table_words
    ):
        score += TABLE_WEIGHT

    return score


def _derived_factor(question_words, document):

    table_words = _name_words(document.metadata.get("key", ""))

    marked = {word for word in table_words if matches(word, DERIVED_MARKERS)}

    if marked and overlap(marked, question_words) == 0:
        return DERIVED_PENALTY

    return 1.0


def _all_documents(vector_store):

    return {
        document.metadata["key"]: document
        for document in vector_store.docstore._dict.values()
        if "key" in document.metadata
    }


def retrieve_candidates(vector_store, question, limit=20, fetch_k=60):
    """Rank tables by embedding similarity fused with keyword matches."""

    all_documents = _all_documents(vector_store)

    question_words = words(question)

    semantic = [
        document.metadata["key"]
        for document, _ in vector_store.similarity_search_with_score(
            question,
            k=min(fetch_k, len(all_documents)),
        )
    ]

    keyword_scores = {
        key: _keyword_score(question_words, document)
        for key, document in all_documents.items()
    }

    keyword = [
        key
        for key, score in sorted(
            keyword_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if score >= MIN_KEYWORD_SCORE
    ]

    fused = {}

    for ranking in (semantic, keyword):

        for position, key in enumerate(ranking):

            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + position)

    for key in fused:

        fused[key] *= _derived_factor(question_words, all_documents[key])

    ordered = sorted(fused, key=fused.get, reverse=True)

    return [all_documents[key] for key in ordered[:limit]]


def add_related_tables(vector_store, documents, max_neighbors):

    all_documents = _all_documents(vector_store)

    selected = {document.metadata.get("key") for document in documents}

    expanded = list(documents)

    added = 0

    for document in documents:

        for key in document.metadata.get("related", []):

            if key in selected or key not in all_documents:
                continue

            if added >= max_neighbors:
                return expanded

            expanded.append(all_documents[key])
            selected.add(key)
            added += 1

    return expanded


def retrieve_documents(vector_store, question, profile=None):

    profile = profile or {}

    candidates = retrieve_candidates(
        vector_store,
        question,
        limit=profile.get("k", 8),
        fetch_k=profile.get("fetch_k", 40),
    )

    return add_related_tables(
        vector_store,
        candidates,
        profile.get("max_neighbors", 6),
    )
