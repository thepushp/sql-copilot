from langchain_community.vectorstores import (
    FAISS,
)


def build_schema_index(
    documents,
    embeddings,
):

    return FAISS.from_documents(
        documents=documents,
        embedding=embeddings,
    )