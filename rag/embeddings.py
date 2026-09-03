import streamlit as st

from langchain_huggingface import (
    HuggingFaceEmbeddings,
)


@st.cache_resource
def get_embeddings():

    return HuggingFaceEmbeddings(
        model_name=(
            "sentence-transformers/"
            "all-MiniLM-L6-v2"
        )
    )