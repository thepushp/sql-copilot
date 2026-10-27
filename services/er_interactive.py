"""Data and HTML for the interactive ER viewer (see services/assets/er_viewer.*)."""

import json
from functools import lru_cache
from pathlib import Path

from services.er_diagram import _edges, _fk_columns, _visible_columns


ASSETS = Path(__file__).resolve().parent / "assets"

MIN_HEIGHT = 380
MAX_HEIGHT = 720

_TYPE_ALIASES = (
    ("character varying", "varchar"),
    ("timestamp without time zone", "timestamp"),
    ("timestamp with time zone", "timestamptz"),
    ("time without time zone", "time"),
    ("time with time zone", "timetz"),
    ("double precision", "double"),
)


def viewer_height(table_count):
    """Frame height in pixels: grows with the diagram, within sensible limits."""

    return max(MIN_HEIGHT, min(MAX_HEIGHT, 180 + 70 * table_count))


def short_type(data_type):

    text = data_type or ""

    for long_name, short_name in _TYPE_ALIASES:
        text = text.replace(long_name, short_name)

    return text


def graph_data(
    index, keys, mode="Keys only", include_inferred=True, focus=None, theme=None,
    summary=None,
    exports=None,
):
    """Plain, JSON-ready description of the tables and relationships to draw."""

    keys = list(keys)
    key_set = set(keys)

    relations = [
        (child, fk, parent)
        for child, fk, parent in _edges(index, include_inferred)
        if child in key_set and parent in key_set
    ]

    tables = []

    for key in keys:

        table = index[key]

        child_columns = _fk_columns(index, key, key_set, include_inferred)
        referenced = {
            fk.foreign_column for _, fk, parent in relations if parent == key
        }

        tables.append(
            {
                "id": key,
                "name": table.name,
                "schema": table.schema,
                "kind": table.kind,
                "columns": [
                    {
                        "name": column.name,
                        "type": short_type(column.data_type),
                        "pk": bool(column.primary_key),
                        "fk": column.name in child_columns,
                    }
                    for column in _visible_columns(
                        table, child_columns | referenced, mode
                    )
                ],
            }
        )

    return {
        "theme": theme if theme in ("light", "dark") else None,
        "summary": summary,
        "exports": exports or {},
        "focus": focus if focus in key_set else (keys[0] if keys else None),
        "showSchema": len({index[key].schema for key in keys}) > 1,
        "tables": tables,
        "edges": [
            {
                "child": child,
                "childColumn": fk.column,
                "parent": parent,
                "parentColumn": fk.foreign_column,
                "inferred": bool(fk.inferred),
            }
            for child, fk, parent in relations
        ],
    }


@lru_cache(maxsize=None)
def _read_asset(name, modified):

    return (ASSETS / name).read_text(encoding="utf-8")


def _asset(name):
    """Asset text, re-read whenever the file changes (no restart while editing)."""

    return _read_asset(name, (ASSETS / name).stat().st_mtime_ns)


def _json_for_html(data):
    """JSON that is safe inside a <script> block, whatever the names contain."""

    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))

    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def viewer_html(data):

    # The data goes in last so that nothing inside it is ever treated as a
    # placeholder.
    return (
        _asset("er_viewer.html")
        .replace("__CSS__", _asset("er_viewer.css"))
        .replace("__JS__", _asset("er_viewer.js"))
        .replace("__DATA__", _json_for_html(data))
    )
