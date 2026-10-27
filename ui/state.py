"""Session state, run settings and datasource lookups shared by every page."""

from dataclasses import dataclass

import streamlit as st

from config.datasource_config import DATASOURCES
from config.privacy import AccessPolicy, PrivacySettings
from services.audit import get_logger


logger = get_logger("app")


@dataclass
class RunSettings:
    datasource_name: str
    embedding_model: str
    provider: str
    model: str
    temperature: float
    api_key: str
    base_url: str
    timeout: int
    profile: dict
    summarize: bool
    smart_select: bool
    datasource_config: dict | None = None
    dialect_key: str = "postgresql"


def init_state():

    defaults = {
        "messages": [],
        "raw_metadata": None,
        "database_metadata": None,
        "access_policy": AccessPolicy(),
        "privacy": PrivacySettings(),
        "schema_index": None,
        "indexed_datasource": None,
        "index_embedding_model": None,
        "pending_question": None,
        "pending_example": None,
        "example_questions": [],
        "model_nonce": 0,
        "custom_datasources": {},
    }

    for key, value in defaults.items():

        if key not in st.session_state:

            st.session_state[key] = value


def is_indexed(datasource_name):

    return (
        st.session_state.schema_index is not None
        and st.session_state.indexed_datasource == datasource_name
    )


def all_datasources():

    merged = dict(DATASOURCES)

    merged.update(st.session_state.custom_datasources)

    return merged
