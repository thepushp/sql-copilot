import json

import pytest

from services import example_store
from services.example_store import (
    add_example,
    find_similar,
    format_examples,
    load_examples,
)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(example_store, "STORE_PATH", tmp_path / "data" / "ex.json")
    example_store._embedding_cache.clear()
    return example_store.STORE_PATH


def test_empty_store(store):
    assert load_examples() == []


def test_add_creates_file_and_dedupes(store):
    assert add_example("Test", "how many cats", "SELECT 1")
    assert not add_example("Test", "how many cats", "SELECT 1")
    assert add_example("Test", "how many cats", "SELECT 2")

    assert len(load_examples()) == 2
    assert json.loads(store.read_text())[0] == {
        "datasource": "Test", "question": "how many cats", "sql": "SELECT 1",
    }


def test_inputs_are_stripped(store):
    add_example("Test", "  q  ", "  SELECT 1  \n")

    assert load_examples()[0]["question"] == "q"
    assert load_examples()[0]["sql"] == "SELECT 1"


def test_corrupt_file_is_treated_as_empty(store):
    store.parent.mkdir(parents=True)
    store.write_text("{not json")

    assert load_examples() == []
    assert add_example("Test", "q", "SELECT 1")
    assert len(load_examples()) == 1


def test_find_similar_is_scoped_by_datasource(fake_embeddings):
    add_example("Test", "how many cats", "SELECT 1")

    assert find_similar(fake_embeddings, "m", "Test", "how many cats")
    assert find_similar(fake_embeddings, "m", "Other", "how many cats") == []


def test_find_similar_requires_similarity(fake_embeddings):
    add_example("Test", "how many cats", "SELECT 1")

    assert find_similar(fake_embeddings, "m", "Test", "completely different words here") == []


def test_best_matches_first_and_limit(fake_embeddings):
    add_example("Test", "q one", "SELECT 1")
    add_example("Test", "q two", "SELECT 2")

    found = find_similar(fake_embeddings, "m", "Test", "q two", limit=1)

    assert [e["sql"] for e in found] == ["SELECT 2"]


def test_limit_zero_or_empty_store(fake_embeddings):
    assert find_similar(fake_embeddings, "m", "Test", "q") == []

    add_example("Test", "q", "SELECT 1")

    assert find_similar(fake_embeddings, "m", "Test", "q", limit=0) == []


class CountingEmbeddings:

    def __init__(self, inner):
        self.inner = inner
        self.document_calls = []

    def embed_documents(self, texts):
        self.document_calls.append(list(texts))
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        return self.inner.embed_query(text)


def test_stored_question_embeddings_are_cached(fake_embeddings):
    counting = CountingEmbeddings(fake_embeddings)
    add_example("Test", "q", "SELECT 1")

    find_similar(counting, "m", "Test", "q")
    find_similar(counting, "m", "Test", "q")

    assert counting.document_calls == [["q"]]


def test_cache_is_per_embedding_model(fake_embeddings):
    counting = CountingEmbeddings(fake_embeddings)
    add_example("Test", "q", "SELECT 1")

    find_similar(counting, "model-a", "Test", "q")
    find_similar(counting, "model-b", "Test", "q")

    assert len(counting.document_calls) == 2


def test_format_examples():
    assert format_examples([]) == "None"
    assert format_examples([{"question": "q", "sql": "SELECT 1"}]) == (
        "Question: q\nSQL: SELECT 1"
    )
