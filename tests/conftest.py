import sqlite3
from types import SimpleNamespace

import pytest

from langchain_community.embeddings import DeterministicFakeEmbedding

from database.schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    SchemaMetadata,
    TableMetadata,
    infer_relationships,
)


SHOP_SQL = """
CREATE TABLE customer(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT DEFAULT 'ACTIVE');
CREATE TABLE product(id INTEGER PRIMARY KEY, title TEXT, price REAL);
CREATE TABLE orders(
    id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customer(id),
    placed_on TEXT, total REAL);
CREATE TABLE order_item(
    id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product_id INTEGER, qty INTEGER);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE VIEW big_orders AS SELECT * FROM orders WHERE total > 100;
INSERT INTO customer VALUES (1,'Ann','ACTIVE'),(2,'Bob','INACTIVE');
INSERT INTO product VALUES (1,'Pen',2.5),(2,'Book',12);
INSERT INTO orders VALUES (1,1,'2026-10-01',150),(2,1,'2026-10-02',20),
    (3,2,'2026-10-03',300);
INSERT INTO order_item VALUES (1,1,1,3),(2,1,2,1),(3,3,2,5);
"""


@pytest.fixture(scope="session")
def shop_db(tmp_path_factory):
    """A small SQLite shop database (tables, FK, view, index)."""

    path = tmp_path_factory.mktemp("db") / "shop.db"

    connection = sqlite3.connect(path)
    connection.executescript(SHOP_SQL)
    connection.commit()
    connection.close()

    return str(path)


@pytest.fixture
def shop_config(shop_db):
    return {"driver": "sqlite", "database": shop_db}


@pytest.fixture
def fake_embeddings():
    return DeterministicFakeEmbedding(size=32)


def make_table(
    name,
    columns=("id",),
    schema="public",
    kind="table",
    foreign_keys=(),
    primary_key="id",
    **extra,
):

    table = TableMetadata(
        schema=schema,
        name=name,
        kind=kind,
        columns=[
            ColumnMetadata(
                name=column,
                data_type="bigint",
                nullable="YES",
                default=None,
                primary_key=(column == primary_key),
            )
            for column in columns
        ],
        foreign_keys=list(foreign_keys),
        **extra,
    )

    return table


def make_metadata(*schemas):
    """schemas: (schema_name, [TableMetadata, ...]) pairs."""

    return DatabaseMetadata(
        schemas=[
            SchemaMetadata(name=name, tables=list(tables))
            for name, tables in schemas
        ]
    )


@pytest.fixture
def sample_metadata():
    """student/school/trip world with explicit and inferrable relationships."""

    student = make_table("student", ("id", "name", "school_id", "password"))
    school = make_table("school", ("id", "name"))
    trip = make_table(
        "trip",
        ("id", "student_id", "status"),
        foreign_keys=[
            ForeignKeyMetadata("student_id", "public", "student", "id")
        ],
    )
    trip.columns[2].values = ["COMPLETED", "CANCELLED"]
    trip.columns[2].values_kind = "common"
    trip.comment = "One row per trip"
    trip.approx_rows = 12000
    trip_log = make_table("trip_log", ("id", "trip_id"))
    old = make_table("old_table", ("id",), flyway_managed=True, flyway_active=False)
    flyway = make_table("flyway_schema_history", ("id",))
    audit = make_table("audit", ("id", "secret_token"), schema="ops")

    metadata = make_metadata(
        ("ops", [audit]),
        ("public", [student, school, trip, trip_log, old, flyway]),
    )

    infer_relationships(metadata)

    return metadata


class ScriptedLLM:
    """Stands in for a chat model. `responder(prompt_text, call_no)` -> str."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def invoke(self, messages):

        text = (
            messages.to_string()
            if hasattr(messages, "to_string")
            else str(messages)
        )

        self.calls.append(text)

        return SimpleNamespace(
            content=self.responder(text, len(self.calls)),
            usage_metadata={"input_tokens": 100, "output_tokens": 10},
        )


@pytest.fixture
def scripted_llm():
    return ScriptedLLM
