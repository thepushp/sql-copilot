from langchain_community.vectorstores import FAISS


def build_schema_index(documents, embeddings):
    """Embed the short search text; keep the full table text in metadata."""

    texts = [
        document.metadata.get("search_text") or document.page_content
        for document in documents
    ]

    metadatas = [
        {**document.metadata, "content": document.page_content}
        for document in documents
    ]

    return FAISS.from_texts(texts, embeddings, metadatas=metadatas)
