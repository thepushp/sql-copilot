from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

from config.datasource_config import DATASOURCES


_engines: dict[str, Engine] = {}


def get_engine(
    datasource_name: str,
) -> Engine:

    if datasource_name in _engines:
        return _engines[datasource_name]

    config = DATASOURCES[
        datasource_name
    ]

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
    )

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

    _engines[datasource_name] = engine

    return engine


@contextmanager
def get_connection(
    datasource_name: str,
):

    engine = get_engine(
        datasource_name
    )

    with engine.connect() as connection:

        yield connection