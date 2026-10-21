from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from config.datasource_config import DATASOURCES
from database.dialects import build_url, get_dialect


_engines: dict[tuple, Engine] = {}


def _cache_key(datasource_name, config):

    return (
        datasource_name,
        config.get("driver"),
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

    dialect = get_dialect(config)

    required = ["database"]

    if dialect.needs_host:
        required += ["host", "username"]

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

    engine = create_engine(
        build_url(config),
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=dict(dialect.connect_args),
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