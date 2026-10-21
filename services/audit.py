"""Application logging and a local, append-only audit trail.

Console logs go to stderr. The audit trail is a JSON-lines file recording what
was asked and what SQL ran, never result rows, passwords or API keys.

  SQL_COPILOT_AUDIT_LOG   path of the audit file, or "off" (default: data/audit.log)
  SQL_COPILOT_LOG_LEVEL   console log level (default: INFO)
"""

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


ROOT_LOGGER = "sql_copilot"
AUDIT_LOGGER = f"{ROOT_LOGGER}.audit"
DEFAULT_AUDIT_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "audit.log"
)

MAX_FIELD_CHARS = 4000
FIELD_LIMITS = {"question": 1000, "error": 500}
SECRET_FIELDS = {"password", "api_key", "key", "token", "secret"}

_configured = False


def get_logger(name: str) -> logging.Logger:

    return logging.getLogger(f"{ROOT_LOGGER}.{name}")


def audit_path() -> Path | None:

    value = os.getenv("SQL_COPILOT_AUDIT_LOG")

    if value is None:
        return DEFAULT_AUDIT_PATH

    if value.strip().lower() in ("", "off", "false", "0", "none"):
        return None

    return Path(value).expanduser()


def configure_logging(force: bool = False) -> None:
    """Idempotent: safe to call on every Streamlit rerun."""

    global _configured

    if _configured and not force:
        return

    root = logging.getLogger(ROOT_LOGGER)
    audit_logger = logging.getLogger(AUDIT_LOGGER)

    for logger in (root, audit_logger):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    root.setLevel(
        getattr(
            logging,
            os.getenv("SQL_COPILOT_LOG_LEVEL", "INFO").upper(),
            logging.INFO,
        )
    )
    root.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(console)

    audit_logger.propagate = False
    audit_logger.setLevel(logging.INFO)

    path = audit_path()

    if path is not None:

        try:

            path.parent.mkdir(parents=True, exist_ok=True)

            handler = RotatingFileHandler(
                path,
                maxBytes=5_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            audit_logger.addHandler(handler)

        except OSError as exc:

            root.warning("Audit log disabled (%s): %s", path, exc)

    _configured = True


def _clean(name: str, value):

    if isinstance(value, str):
        return value[: FIELD_LIMITS.get(name, MAX_FIELD_CHARS)]

    if isinstance(value, (list, tuple, set)):
        return [_clean(name, item) for item in list(value)[:50]]

    return value


def audit(event: str, **fields) -> None:
    """Append one JSON line. Never raises."""

    try:

        configure_logging()

        logger = logging.getLogger(AUDIT_LOGGER)

        if not logger.handlers:
            return

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
        }

        for name, value in fields.items():

            if name.lower() in SECRET_FIELDS or value is None:
                continue

            record[name] = _clean(name, value)

        logger.info(json.dumps(record, default=str, ensure_ascii=False))

    except Exception:

        get_logger("audit").exception("Could not write audit event")
