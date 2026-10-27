import json

import pytest
from langchain_community.embeddings import DeterministicFakeEmbedding

from services import example_store, favorites, feedback_store
from services.feedback_store import (
    MAX_KEPT,
    add_rejection,
    find_similar_rejections,
    format_rejections,
    load_rejections,
    remove_rejection,
)


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_store, "STORE_PATH", tmp_path / "data" / "rejected.json")
    monkeypatch.setattr(favorites, "FAVORITES_PATH", tmp_path / "data" / "fav.json")
    example_store._embedding_cache.clear()
    return tmp_path


def test_empty():
    assert load_rejections() == []
    assert format_rejections([]) == "None"


def test_add_stores_reason_and_replaces_the_same_answer():
    add_rejection("Shop", "how many cats", "SELECT 1", "Wrong tables", "use pets")
    add_rejection("Shop", " how many cats ", " SELECT 1 ", "Other", "")

    entries = load_rejections()

    assert len(entries) == 1
    assert entries[0]["reason"] == "Other" and entries[0]["sql"] == "SELECT 1"


def test_remove():
    add_rejection("Shop", "q", "SELECT 1", "Other")

    assert remove_rejection("Shop", "q", "SELECT 1") is True
    assert remove_rejection("Shop", "q", "SELECT 1") is False
    assert load_rejections() == []


def test_the_store_is_capped():
    for number in range(MAX_KEPT + 20):
        add_rejection("Shop", f"q{number}", "SELECT 1", "Other")

    assert len(load_rejections()) == MAX_KEPT
    assert load_rejections()[-1]["question"] == f"q{MAX_KEPT + 19}"


def test_only_similar_questions_for_the_same_datasource_are_found():
    add_rejection("Shop", "list the customers", "SELECT bad", "Wrong tables")
    add_rejection("Other", "list the customers", "SELECT other", "Wrong tables")
    embeddings = DeterministicFakeEmbedding(size=32)

    found = find_similar_rejections(embeddings, "fake", "Shop", "list the customers")

    assert [entry["sql"] for entry in found] == ["SELECT bad"]
    assert find_similar_rejections(embeddings, "fake", "Shop", "zzz qqq unrelated") == []


def test_format_includes_sql_and_why():
    add_rejection("Shop", "q", "SELECT bad", "Wrong filter or value", "only active")

    text = format_rejections(load_rejections())

    assert "Wrong SQL: SELECT bad" in text and "Wrong filter or value - only active" in text


def test_damaged_file_is_ignored(paths):
    feedback_store.STORE_PATH.parent.mkdir(parents=True)
    feedback_store.STORE_PATH.write_text("not json")

    assert load_rejections() == []


def test_favorites_round_trip_per_datasource():
    favorites.save_favorites("Shop", ["main.a", "main.b"])
    favorites.save_favorites("Other", ["x.y"])

    assert favorites.load_favorites("Shop") == ["main.a", "main.b"]
    assert favorites.load_favorites("Other") == ["x.y"]
    assert favorites.load_favorites("Nobody") == []


def test_favorites_survive_garbage(paths):
    favorites.FAVORITES_PATH.parent.mkdir(parents=True)
    favorites.FAVORITES_PATH.write_text(json.dumps(["not", "a", "dict"]))

    assert favorites.load_favorites("Shop") == []

    favorites.FAVORITES_PATH.write_text("{broken")

    assert favorites.load_favorites("Shop") == []
