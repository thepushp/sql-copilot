"""Starred tables per datasource, kept in a small file so they survive a restart.

Like the glossary and approved examples, the file belongs to the server, not to
a user: everyone using this server shares one list per datasource.
"""

import json
import os
from pathlib import Path


FAVORITES_PATH = Path(
    os.getenv(
        "FAVORITES_PATH",
        Path(__file__).resolve().parent.parent / "data" / "favorites.json",
    )
)


def _load_all() -> dict:

    if not FAVORITES_PATH.exists():
        return {}

    try:
        data = json.loads(FAVORITES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def load_favorites(datasource: str) -> list[str]:

    keys = _load_all().get(datasource, [])

    return [key for key in keys if isinstance(key, str)]


def save_favorites(datasource: str, keys: list[str]) -> None:

    data = _load_all()

    data[datasource] = list(keys)

    try:
        FAVORITES_PATH.parent.mkdir(parents=True, exist_ok=True)

        temporary = FAVORITES_PATH.with_suffix(".tmp")

        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")

        temporary.replace(FAVORITES_PATH)
    except OSError:
        pass
