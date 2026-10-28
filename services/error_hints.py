"""Turns raw error text into a short "what to try" list.

The raw message is always kept and shown; the tips only add direction. Matching
is by plain phrases that drivers and providers use, so it is a best effort and
falls back to general advice for each kind of error.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Hint:
    tips: list = field(default_factory=list)
    matched: bool = False


# (pattern, tips) per kind. First match wins.
_RULES = {
    "database": [
        (r"password authentication failed|access denied|login failed|ora-01017|authentication failed|invalid password|invalid credentials",
         ["The server rejected the username or password. Re-enter them in **Add a connection** (or check `.env`).",
          "Make sure this user is allowed to connect to that database from this machine."]),
        (r"connection refused|could not connect to server|can't connect to mysql|unable to connect|ora-12541|no route to host|network is unreachable",
         ["Nothing accepted the connection. Check the host and port, and that the database server is running.",
          "If it is on a private network, connect to the VPN first. From Docker, use `host.docker.internal` for a database on your computer."]),
        (r"timed out|timeout expired|login timeout|ora-12170",
         ["The server did not answer in time. Check the host and port, a firewall or VPN, and that the server is not overloaded."]),
        (r"name or service not known|nodename nor servname|getaddrinfo failed|could not translate host|unknown host|temporary failure in name resolution",
         ["The host name could not be found. Check it for typos, or use the server's IP address."]),
        (r"database \".*\" does not exist|unknown database|cannot open database|ora-12514|ora-12505|service name",
         ["The server is reachable but that database name was not found. Check the database name (for Oracle, the service name)."]),
        (r"unable to open database file|file not found|no such file|file is not a database|readonly database|is a directory",
         ["For SQLite the database is a file path that must already exist and be readable. Check the path."]),
        (r"ssl|certificate|tls",
         ["There is a problem with the secure connection. Try a different **SSL mode** in the connection form (for example `prefer` or `disable` on a local server)."]),
        (r"no module named|driver|dll load failed|library not loaded|cannot import",
         ["A database driver is missing. Run `pip install -r requirements.txt` in the app's environment and restart the app."]),
        (r"permission denied|insufficient privileges|ora-00942|not authorized|denied",
         ["This user is not allowed to read what the app needs. Grant `SELECT` on the tables (and read access to the catalog) or use another user."]),
        (r"no tables to index|nothing to index",
         ["Check that this user can read at least one table.",
          "Open **Privacy** in the sidebar and make sure the exclusions do not hide everything."]),
    ],
    "model": [
        (r"connection refused|failed to connect|connecterror|cannot connect|connection error|11434|max retries exceeded",
         ["The model server could not be reached. For Ollama, start it (`ollama serve`) and check **Model & provider → Ollama URL**.",
          "Or pick a hosted provider in **Model & provider**."]),
        (r"not found|404|unknown model|does not exist|pull the model|model_not_found",
         ["That model is not available on this provider. For Ollama run `ollama pull <model>`; otherwise pick another model in **Model & provider**."]),
        (r"401|unauthorized|invalid api key|incorrect api key|invalid x-api-key|api key|authentication",
         ["The provider rejected the API key. Enter a valid key in **Model & provider** (or set it in `.env`)."]),
        (r"429|rate limit|quota|too many requests|overloaded|insufficient_quota|billing",
         ["The provider is limiting requests or your quota is used up. Wait a moment and try again, or switch model or provider."]),
        (r"context length|maximum context|too many tokens|prompt is too long|context_length",
         ["The question plus schema was too big for this model. In **Advanced**, choose a smaller **Context size**, or use a model with a larger window."]),
        (r"timed out|timeout|read timeout",
         ["The model took too long. Raise the model timeout in **Advanced**, try a smaller **Context size**, or use a faster model."]),
        (r"empty sql|returned empty",
         ["The model did not return a query. Try again, rephrase the question, or pick a stronger model."]),
    ],
    "query": [
        (r"statement timeout|canceling statement|query timeout|max_execution_time|timed out|timeout",
         ["The query ran longer than the time limit. Ask for less data: add a filter (a date range or status) or a smaller row count."]),
        (r"does not exist|no such table|no such column|unknown column|invalid object name|invalid identifier|undefined column|undefined table|ora-00904|ora-00942",
         ["The SQL names a table or column the database does not have. If the schema changed, click **Discover & index database** again.",
          "You can check the real names in the **Explorer**."]),
        (r"syntax error|syntax|ora-009|you have an error in your sql",
         ["The SQL is not valid for this database. Edit it in the SQL box and run it again, or rephrase the question."]),
        (r"permission denied|not authorized|insufficient privileges|denied",
         ["This database user may not read that table or column. Ask for different data or use a user with access."]),
        (r"division by zero|divide by zero",
         ["The query divides by zero for some rows. Add a condition that excludes zero values, or use `NULLIF(x, 0)`."]),
        (r"operator does not exist|cannot be cast|invalid input syntax|conversion failed|data type mismatch|type mismatch",
         ["A value was compared or converted using the wrong type, for example text against a number. Check the column types in the **Explorer**."]),
    ],
    "guard": [
        (r"excluded by the privacy settings",
         ["The question needs something your **Privacy** settings hide. Ask about other data, or change the exclusions in the sidebar."]),
        (r"system catalog",
         ["Direct reads of system tables are blocked. Use the **Explorer** for schema information, or set `SQL_COPILOT_ALLOW_SYSTEM_CATALOGS` if you really need them."]),
        (r"only select|data-modifying|insert|update|delete|select into|copy is not allowed|operation:",
         ["This tool only reads data and never changes it. Rephrase as a question about what the data contains."]),
        (r"function .* is not allowed|locking reads",
         ["The generated SQL used something that is not allowed. Rephrase the question, or write a plain `SELECT` yourself."]),
        (r"invalid .* sql|sql is empty",
         ["The generated SQL could not be parsed. Try asking again or rephrase the question."]),
    ],
}

_GENERAL = {
    "database": [
        "Check the connection details in the sidebar (host, port, database, user) and that the server is reachable from this machine.",
        "You can test a new connection with **Add a connection → Test**.",
    ],
    "model": [
        "Check the provider, model name and API key in **Model & provider**, then try again.",
        "If it keeps failing, pick another model or provider.",
    ],
    "query": [
        "Edit the SQL in the box and run it again, or rephrase the question.",
        "The **Explorer** shows the real table and column names.",
    ],
    "guard": [
        "This tool only reads data. Rephrase your question as a request to look something up.",
    ],
    "filter": [
        "Write only the condition (the part after WHERE), for example `status = 'OPEN' AND total > 100`.",
        "Use single quotes for text and the exact column names shown in **Properties**.",
    ],
}


def explain(text, kind="query"):
    """Tips for an error text. `kind` is database, model, query or guard."""

    lowered = (text or "").lower()

    rules = _RULES.get(kind, [])

    if kind in ("query", "filter"):

        # Safety-guard refusals can also reach a plain query error.
        rules = rules + _RULES["guard"]

    for pattern, tips in rules:

        if re.search(pattern, lowered):

            return Hint(list(tips), matched=True)

    return Hint(list(_GENERAL.get(kind, [])), matched=False)


def with_tips(text, kind="query"):
    """The error text followed by a "What to try" list (markdown)."""

    hint = explain(text, kind)

    if not hint.tips:
        return text

    return f"{text}\n\n**What to try**\n" + "\n".join(f"- {tip}" for tip in hint.tips)


NO_SQL_TIPS = [
    "Name the table or column you mean, for example \"how many rows in orders\".",
    "Search the **Explorer** for the right table (it also searches column names and known values).",
    "If your business words differ from the table names, define them in the **Business glossary**.",
    "Check that **Privacy** is not excluding the table you need.",
]

EMPTY_RESULT_TIPS = [
    "Loosen a filter, or check the spelling of a value (the **Explorer** profile shows the values that exist).",
    "Ask what exists first, for example \"which statuses are there?\".",
]
