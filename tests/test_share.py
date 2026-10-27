from ui.share import decode, encode, validate


def test_round_trip_around_table():
    params = encode("Around a table", table="public.orders", depth=2)
    link = decode(params)
    assert link["mode"] == "Around a table"
    assert validate(link, {"public.orders"}, {"public"}) == {
        "er_mode": "Around a table",
        "er_table": "public.orders",
        "er_depth": 2,
    }


def test_round_trip_schema():
    link = decode(encode("Whole schema", schema="sales"))
    assert validate(link, set(), {"sales"}) == {
        "er_mode": "Whole schema",
        "er_schema": "sales",
    }


def test_unknown_values_are_dropped():
    link = decode(
        {"view": "er", "mode": "table", "table": "x.gone", "depth": "9", "schema": "nope"}
    )
    assert validate(link, {"public.a"}, {"public"}) == {"er_mode": "Around a table"}


def test_not_an_er_link():
    assert decode({}) is None
    assert decode({"view": "chat"}) is None


def test_bad_mode_falls_back():
    assert decode({"view": "er", "mode": "zzz"})["mode"] == "Around a table"
