import pytest

from services.error_hints import explain, with_tips


@pytest.mark.parametrize(
    "text, kind, phrase",
    [
        ('FATAL: password authentication failed for user "x"', "database", "username or password"),
        ("(psycopg.OperationalError) connection failed: Connection refused", "database", "running"),
        ("could not translate host name \"db.internal\" to address", "database", "host name"),
        ('database "shop" does not exist', "database", "database name"),
        ("unable to open database file", "database", "path"),
        ("SQLite file not found: /tmp/x.db", "database", "path"),
        ("SSL SYSCALL error: EOF detected", "database", "SSL mode"),
        ("No module named 'oracledb'", "database", "pip install"),
        ("There are no tables to index. Check that this user", "database", "Privacy"),
        ("httpx.ConnectError: [Errno 61] Connection refused (localhost:11434)", "model", "ollama serve"),
        ("model 'llama9' not found, try pulling it first", "model", "ollama pull"),
        ("Error code: 401 - invalid api key", "model", "API key"),
        ("Error code: 429 - rate limit exceeded", "model", "Wait"),
        ("This model's maximum context length is 8192 tokens", "model", "Context size"),
        ("canceling statement due to statement timeout", "query", "filter"),
        ('relation "orders" does not exist', "query", "index"),
        ("syntax error at or near \"FORM\"", "query", "not valid"),
        ("division by zero", "query", "NULLIF"),
        ("Table main.secret is excluded by the privacy settings.", "guard", "Privacy"),
        ("pg_class is a system catalog. Use the Database Explorer", "guard", "SQL_COPILOT_ALLOW_SYSTEM_CATALOGS"),
        ("Only SELECT/WITH queries can execute. Detected operation: DELETE", "guard", "only reads"),
    ],
)
def test_known_errors_get_specific_tips(text, kind, phrase):
    hint = explain(text, kind)

    assert hint.matched, text
    assert any(phrase.lower() in tip.lower() for tip in hint.tips), hint.tips


@pytest.mark.parametrize("kind", ["database", "model", "query", "guard"])
def test_unknown_errors_get_general_advice(kind):
    hint = explain("something nobody has seen before 12345", kind)

    assert not hint.matched and hint.tips


def test_unknown_kind_and_empty_text_do_not_crash():
    assert explain("", "nonsense").tips == []
    assert explain(None, "database").tips


def test_with_tips_keeps_the_original_text_first():
    text = with_tips("password authentication failed", "database")

    assert text.startswith("password authentication failed")
    assert "**What to try**" in text and text.count("\n- ") >= 1


def test_with_tips_returns_text_unchanged_without_tips():
    assert with_tips("x", "nonsense") == "x"
