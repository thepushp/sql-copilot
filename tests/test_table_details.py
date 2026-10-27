"""Constraints, view definitions and sizes in the metadata."""

import sqlite3

import pytest

from config.privacy import PrivacySettings, apply_privacy
from database.database_inspector import inspect_database
from database.schema_metadata import (
    ColumnMetadata,
    ConstraintMetadata,
    DatabaseMetadata,
    SchemaMetadata,
    TableMetadata,
)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "d.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE person(
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE,
            age INTEGER CHECK (age >= 0),
            password TEXT
        );
        CREATE VIEW adults AS SELECT id, email FROM person WHERE age >= 18;
        CREATE VIEW logins AS SELECT id, password FROM person;
        """
    )
    con.commit()
    con.close()
    return {"driver": "sqlite", "database": str(path)}


def tables(metadata):
    return {t.name: t for s in metadata.schemas for t in s.tables}


def test_unique_and_check_constraints_are_found(config):
    person = tables(inspect_database("d", config))["person"]

    kinds = {c.kind: c.definition for c in person.constraints}

    assert "email" in kinds["unique"]
    assert "age >= 0" in kinds["check"]


def test_view_definition_is_found(config):
    found = tables(inspect_database("d", config))

    assert "age >= 18" in found["adults"].definition
    assert found["person"].definition is None


def test_privacy_withholds_definitions_that_name_hidden_columns(config):
    metadata, _ = apply_privacy(inspect_database("d", config), PrivacySettings())
    found = tables(metadata)

    assert found["adults"].definition is not None
    assert found["logins"].definition is None


def test_privacy_withholds_definitions_that_name_excluded_tables(config):
    settings = PrivacySettings(excluded_tables=["main.person"])

    metadata, _ = apply_privacy(inspect_database("d", config), settings)

    assert all(t.definition is None for t in tables(metadata).values())


def test_constraints_naming_hidden_columns_are_dropped():
    table = TableMetadata(
        schema="s",
        name="t",
        columns=[
            ColumnMetadata("id", "int", "NO", None),
            ColumnMetadata("secret_key", "text", "YES", None),
        ],
        constraints=[
            ConstraintMetadata("c1", "unique", "UNIQUE (secret_key)"),
            ConstraintMetadata("c2", "check", "CHECK (id > 0)"),
        ],
    )
    metadata = DatabaseMetadata(schemas=[SchemaMetadata("s", [table])])

    filtered, _ = apply_privacy(metadata, PrivacySettings())

    assert [c.name for c in filtered.schemas[0].tables[0].constraints] == ["c2"]


def test_triggers_are_found_and_withheld_when_they_name_hidden_columns(tmp_path):
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE person(id INTEGER PRIMARY KEY, name TEXT, password TEXT);
        CREATE TABLE audit(note TEXT);
        CREATE TRIGGER log_rename AFTER UPDATE OF name ON person
            BEGIN INSERT INTO audit VALUES ('renamed'); END;
        CREATE TRIGGER log_login AFTER UPDATE OF password ON person
            BEGIN INSERT INTO audit VALUES ('login'); END;
        """
    )
    con.commit()
    con.close()
    config = {"driver": "sqlite", "database": str(path)}

    raw = tables(inspect_database("t", config))

    assert {t.name for t in raw["person"].triggers} == {"log_rename", "log_login"}
    assert raw["audit"].triggers == []

    filtered, _ = apply_privacy(inspect_database("t", config), PrivacySettings())

    assert [t.name for t in tables(filtered)["person"].triggers] == ["log_rename"]
