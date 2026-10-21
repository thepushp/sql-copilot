from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

from config.datasource_config import DATASOURCES


_engines: dict[tuple, Engine] = {}


def _cache_key(datasource_name, config):

    return (
        datasource_name,
        config.get("host"),
        config.get("port"),
        config.get("database"),
        config.get("username"),
        config.get("password"),
        config.get("sslmode"),
    )


def get_engine(
    datasource_name: str,
    config: dict | None = None,
) -> Engine:

    config = config or DATASOURCES[datasource_name]

    cache_key = _cache_key(datasource_name, config)

    if cache_key in _engines:
        return _engines[cache_key]

    required = [
        "host",
        "database",
        "username",
    ]

    missing = [
        key
        for key in required
        if not config.get(key)
    ]

    if missing:

        raise ValueError(
            "Missing datasource configuration: "
            + ", ".join(missing)
        )

    url = URL.create(
        "postgresql+psycopg",
        username=config["username"],
        password=config["password"] or None,
        host=config["host"],
        port=config["port"],
        database=config["database"],
        query=(
            {"sslmode": config["sslmode"]}
            if config.get("sslmode")
            else {}
        ),
    )

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10},
    )

    _engines[cache_key] = engine

    return engine


@contextmanager
def get_connection(
    datasource_name: str,
    config: dict | None = None,
):

    engine = get_engine(
        datasource_name,
        config,
    )

    with engine.connect() as connection:

        yield connection