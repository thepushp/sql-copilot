def create_retriever(
    vector_store,
):

    return vector_store.as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": 5,
            "fetch_k": 15,
        },
    )