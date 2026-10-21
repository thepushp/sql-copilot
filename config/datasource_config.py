import os

from dotenv import load_dotenv

from database.dialects import default_port


load_dotenv()


def _env(
    name: str,
    default: str = "",
) -> str:

    return os.getenv(
        name,
        default,
    )


def _datasource(prefix: str) -> dict:

    driver = _env(f"{prefix}_DB_DRIVER", "postgresql").strip().lower()

    fallback_port = default_port(driver)

    port = _env(f"{prefix}_DB_PORT") or (
        str(fallback_port) if fallback_port else ""
    )

    return {
        "driver": driver,
        "host": _env(f"{prefix}_DB_HOST", "localhost"),
        "port": int(port) if port else None,
        "database": _env(f"{prefix}_DB_NAME"),
        "username": _env(f"{prefix}_DB_USERNAME"),
        "password": _env(f"{prefix}_DB_PASSWORD"),
    }


DATASOURCES = {
    "Development": _datasource("DEV"),
    "Test": _datasource("TEST"),
}
