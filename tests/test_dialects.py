import pytest

from database.dialects import (
    DIALECTS,
    build_url,
    default_port,
    get_dialect,
    is_configured,
)


def config(driver, **overrides):
    base = {
        "driver": driver,
        "host": "db.example.com",
        "port": DIALECTS[driver].default_port,
        "database": "appdb",
        "username": "reader",
        "password": "p@ss/w:rd",
    }
    base.update(overrides)
    return base


def test_all_dialects_have_required_settings():
    for dialect in DIALECTS.values():
        assert dialect.label and dialect.sqlglot and dialect.scheme
        assert dialect.limit_hint
        assert dialect.setup, f"{dialect.key} has no read-only setup"


def test_get_dialect_defaults_to_postgresql():
    assert get_dialect().key == "postgresql"
    assert get_dialect({}).key == "postgresql"
    assert get_dialect({"driver": None}).key == "postgresql"
    assert get_dialect("no-such-db").key == "postgresql"
    assert get_dialect("mysql").key == "mysql"
    assert get_dialect({"driver": "oracle"}).key == "oracle"


def test_default_ports():
    assert default_port("postgresql") == 5432
    assert default_port("mysql") == 3306
    assert default_port("mssql") == 1433
    assert default_port("oracle") == 1521
    assert default_port("sqlite") is None


@pytest.mark.parametrize(
    "driver,scheme",
    [
        ("postgresql", "postgresql+psycopg"),
        ("mysql", "mysql+pymysql"),
        ("mssql", "mssql+pymssql"),
        ("oracle", "oracle+oracledb"),
    ],
)
def test_url_scheme_host_and_special_password(driver, scheme):
    url = build_url(config(driver))

    assert url.drivername == scheme
    assert url.host == "db.example.com"
    assert url.username == "reader"
    assert url.password == "p@ss/w:rd"


def test_empty_password_becomes_no_password():
    assert build_url(config("mysql", password="")).password is None


def test_postgres_sslmode_is_passed_through():
    url = build_url(config("postgresql", sslmode="require"))

    assert url.query["sslmode"] == "require"


def test_sslmode_ignored_for_other_dialects():
    assert "sslmode" not in build_url(config("mysql", sslmode="require")).query


def test_oracle_uses_service_name_not_database():
    url = build_url(config("oracle", database="ORCLPDB1"))

    assert url.query["service_name"] == "ORCLPDB1"
    assert url.database is None


def test_port_falls_back_to_dialect_default():
    assert build_url(config("mssql", port=None)).port == 1433


def test_sqlite_url_is_read_only(shop_db):
    url = build_url({"driver": "sqlite", "database": shop_db})

    assert url.query["mode"] == "ro"
    assert url.query["uri"] == "true"
    assert url.database == f"file:{shop_db}"


def test_sqlite_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_url({"driver": "sqlite", "database": str(tmp_path / "no.db")})


def test_is_configured():
    assert is_configured(config("postgresql"))
    assert not is_configured(config("postgresql", host=""))
    assert not is_configured(config("postgresql", username=""))
    assert not is_configured(config("postgresql", database=""))
    assert not is_configured(None)
    assert not is_configured({})


def test_sqlite_only_needs_a_path():
    assert is_configured({"driver": "sqlite", "database": "x.db"})
    assert not is_configured({"driver": "sqlite", "database": ""})


def test_empty_password_is_still_configured():
    assert is_configured(config("postgresql", password=""))
