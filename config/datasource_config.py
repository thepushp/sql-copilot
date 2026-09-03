import os

from dotenv import load_dotenv


load_dotenv()


def _env(
    name: str,
    default: str = "",
) -> str:

    return os.getenv(
        name,
        default,
    )


DATASOURCES = {

    "Development": {

        "driver": "postgresql",

        "host": _env(
            "DEV_DB_HOST",
            "localhost",
        ),

        "port": int(
            _env(
                "DEV_DB_PORT",
                "5432",
            )
        ),

        "database": _env(
            "DEV_DB_NAME"
        ),

        "username": _env(
            "DEV_DB_USERNAME"
        ),

        "password": _env(
            "DEV_DB_PASSWORD"
        ),
    },

    "Test": {

        "driver": "postgresql",

        "host": _env(
            "TEST_DB_HOST",
            "localhost",
        ),

        "port": int(
            _env(
                "TEST_DB_PORT",
                "5432",
            )
        ),

        "database": _env(
            "TEST_DB_NAME"
        ),

        "username": _env(
            "TEST_DB_USERNAME"
        ),

        "password": _env(
            "TEST_DB_PASSWORD"
        ),
    },
}