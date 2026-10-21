"""Turn a result table into downloadable files.

Databases return values that pandas' writers cannot handle directly (UUIDs,
arrays, JSON documents, IP addresses, raw bytes, time zones). They are
converted here, once, so every format gets readable and valid output.
"""

import datetime
import decimal
import io
import ipaddress
import json
import re
import uuid

import numpy as np
import pandas as pd

EXPORT_ROW_LIMIT = 10_000

_CONTAINERS = (list, tuple, set, frozenset, dict)


def _is_missing(value):

    if isinstance(value, _CONTAINERS) or isinstance(value, (bytes, bytearray, memoryview)):
        return False

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _plain(value):
    """A JSON-safe equivalent of any value a database driver can return."""

    if value is None or _is_missing(value):
        return None

    if isinstance(value, np.generic):
        return _plain(value.item())

    if isinstance(value, (str, bool, int, float)):
        return value

    if isinstance(value, decimal.Decimal):
        if value == value.to_integral_value() and abs(value) < 2**53:
            return int(value)
        return float(value) if len(value.as_tuple().digits) <= 15 else str(value)

    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()

    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}

    if isinstance(value, _CONTAINERS):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [_plain(item) for item in items]

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time, pd.Timestamp)):
        return value.isoformat()

    if isinstance(value, datetime.timedelta):
        return pd.Timedelta(value).isoformat()

    if isinstance(value, (uuid.UUID, ipaddress._BaseAddress, ipaddress._BaseNetwork)):
        return str(value)

    return str(value)


def _cell(value):
    """A value for CSV and Excel: containers become JSON text, the rest stays as is."""

    if isinstance(value, _CONTAINERS):
        return json.dumps(_plain(value), ensure_ascii=False)

    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()

    if isinstance(value, (uuid.UUID, ipaddress._BaseAddress, ipaddress._BaseNetwork, datetime.timedelta)):
        return str(value)

    return value


def _converted(frame, converter):

    result = frame.copy()

    for name in result.columns:

        if result[name].dtype == object:
            result[name] = result[name].map(converter)

    return result


def to_csv(frame):

    return _converted(frame, _cell).to_csv(index=False).encode("utf-8-sig")


def to_json(frame):

    return _converted(frame, _plain).to_json(
        orient="records",
        date_format="iso",
        force_ascii=False,
        indent=2,
    ).encode("utf-8")


def to_excel(frame):
    """An .xlsx file. Time zones are dropped because Excel cannot store them."""

    safe = _converted(frame, _cell)

    for name in safe.columns:

        column = safe[name]

        if getattr(column.dtype, "tz", None) is not None:
            safe[name] = column.dt.tz_localize(None)
        elif column.dtype == object:
            safe[name] = column.map(_excel_value)

    buffer = io.BytesIO()

    safe.to_excel(buffer, index=False, sheet_name="Data")

    return buffer.getvalue()


def _excel_value(value):

    if isinstance(value, datetime.datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)

    if isinstance(value, datetime.time) and value.tzinfo is not None:
        return value.replace(tzinfo=None)

    return value


FORMATS = (
    ("CSV", "csv", "text/csv", to_csv),
    ("JSON", "json", "application/json", to_json),
    (
        "Excel",
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        to_excel,
    ),
)


def safe_file_name(name):
    """A table name is not always a legal file name ("a/b", "my table")."""

    return re.sub(r"[^\w.-]+", "_", str(name)).strip("._") or "export"


def build_exports(frame, base_name):
    """[(label, filename, mime, bytes)] for every supported format."""

    base_name = safe_file_name(base_name)

    return [
        (label, f"{base_name}.{extension}", mime, converter(frame))
        for label, extension, mime, converter in FORMATS
    ]
