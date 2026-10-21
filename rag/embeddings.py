import os

import streamlit as st

from langchain_huggingface import HuggingFaceEmbeddings


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

EMBEDDING_CHOICES = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-small-en-v1.5",
    "BAAI/bge-base-en-v1.5",
    "sentence-transformers/all-mpnet-base-v2",
]


def default_embedding_model():

    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


@st.cache_resource
def get_embeddings(model_name=None):

    return HuggingFaceEmbeddings(
        model_name=model_name or default_embedding_model(),
        encode_kwargs={"normalize_embeddings": True},
    )
