"""Shareable ER-diagram links: the diagram state lives in the page URL."""

MODES = {
    "table": "Around a table",
    "schema": "Whole schema",
    "answer": "Tables from last answer",
}

_MODE_CODES = {label: code for code, label in MODES.items()}

PARAM_NAMES = ("view", "mode", "table", "schema", "depth")


def encode(mode, table=None, schema=None, depth=None):
    """Query parameters that describe the current diagram."""

    params = {"view": "er", "mode": _MODE_CODES.get(mode, "table")}

    if mode == "Around a table" and table:

        params["table"] = table
        params["depth"] = str(depth or 1)

    elif mode == "Whole schema" and schema:

        params["schema"] = schema

    return params


def decode(params):
    """The raw requested state, or None when the URL is not an ER link."""

    if params.get("view") != "er":

        return None

    return {
        "mode": MODES.get(params.get("mode", "table"), MODES["table"]),
        "table": params.get("table"),
        "schema": params.get("schema"),
        "depth": params.get("depth"),
    }


def validate(link, tables, schemas):
    """Session-state values from a decoded link, keeping only what exists."""

    values = {"er_mode": link["mode"]}

    if link.get("table") in tables:

        values["er_table"] = link["table"]

    if link.get("schema") in schemas:

        values["er_schema"] = link["schema"]

    try:

        depth = int(link.get("depth"))

    except (TypeError, ValueError):

        depth = None

    if depth in (1, 2, 3):

        values["er_depth"] = depth

    return values
