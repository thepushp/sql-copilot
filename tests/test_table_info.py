from conftest import make_metadata, make_table
from database.schema_metadata import ForeignKeyMetadata
from services.er_diagram import table_index
from services.table_info import column_targets, incoming, outgoing


def world():
    customer = make_table("customer")
    orders = make_table(
        "orders", ("id", "customer_id", "owner_id"),
        foreign_keys=[
            ForeignKeyMetadata("customer_id", "public", "customer", "id"),
            ForeignKeyMetadata("owner_id", "public", "customer", "id", inferred=True),
            ForeignKeyMetadata("ghost_id", "public", "ghost", "id"),
        ],
    )
    review = make_table(
        "review", ("id", "customer_id"),
        foreign_keys=[ForeignKeyMetadata("customer_id", "public", "customer", "id")],
    )
    return table_index(make_metadata(("public", [customer, orders, review])))


def test_outgoing_lists_known_targets_only():
    index = world()

    refs = outgoing(index["public.orders"], index)

    assert [(r.column, r.other_key, r.other_column, r.inferred) for r in refs] == [
        ("customer_id", "public.customer", "id", False),
        ("owner_id", "public.customer", "id", True),
    ]


def test_incoming_lists_who_points_here_with_real_keys_first():
    index = world()

    refs = incoming("public.customer", index)

    assert [(r.other_key, r.other_column, r.inferred) for r in refs] == [
        ("public.orders", "customer_id", False),
        ("public.review", "customer_id", False),
        ("public.orders", "owner_id", True),
    ]
    assert all(r.column == "id" for r in refs)


def test_nothing_points_at_a_leaf():
    assert incoming("public.review", world()) == []


def test_column_targets_for_the_column_grid():
    index = world()

    assert column_targets(index["public.orders"], index) == {
        "customer_id": "customer.id",
        "owner_id": "customer.id (inferred)",
    }
