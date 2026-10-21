import json
import logging
from logging.handlers import RotatingFileHandler

import pytest

from database.sql_guard import validate_sql
from services import audit as audit_module
from services.audit import audit, audit_path, configure_logging


def events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestSystemCatalogAndLockGuard:

    @pytest.mark.parametrize(
        "dialect,sql",
        [
            ("postgresql", "SELECT pg_advisory_lock(42)"),
            ("postgresql", "SELECT pg_advisory_xact_lock(1)"),
            ("postgresql", "SELECT pg_try_advisory_lock(1)"),
            ("postgresql", "SELECT pg_notify('a', 'b')"),
            ("postgresql", "SELECT lo_get(1)"),
            ("postgresql", "SELECT * FROM information_schema.columns"),
            ("postgresql", "SELECT * FROM pg_catalog.pg_tables"),
            ("postgresql", "SELECT * FROM pg_tables"),
            ("postgresql", "SELECT * FROM pg_stat_activity"),
            ("mysql", "SELECT * FROM mysql.user"),
            ("mysql", "SELECT * FROM information_schema.tables"),
            ("mysql", "SELECT release_all_locks()"),
            ("mssql", "SELECT * FROM sys.tables"),
            ("mssql", "SELECT * FROM master.dbo.sysdatabases"),
            ("mssql", "SELECT sp_who()"),
            ("sqlite", "SELECT * FROM sqlite_master"),
            ("oracle", "SELECT * FROM dba_tables"),
            ("oracle", "SELECT * FROM v$session"),
            ("oracle", "SELECT * FROM sys.obj$"),
            ("oracle", "SELECT dbms_lock.sleep(5) FROM dual"),
        ],
    )
    def test_blocked(self, dialect, sql):
        assert not validate_sql(sql, None, dialect).allowed

    @pytest.mark.parametrize(
        "dialect,sql",
        [
            ("postgresql", "SELECT * FROM public.trip"),
            ("postgresql", "SELECT version(), current_setting('server_version')"),
            ("postgresql", "WITH pg_x AS (SELECT 1 AS a) SELECT * FROM pg_x"),
            ("mysql", "SELECT * FROM shop.orders"),
            ("mssql", "SELECT * FROM dbo.orders"),
            ("sqlite", "SELECT * FROM orders"),
            ("oracle", "SELECT * FROM orders"),
        ],
    )
    def test_ordinary_queries_still_allowed(self, dialect, sql):
        assert validate_sql(sql, None, dialect).allowed

    def test_reason_points_to_the_explorer(self):
        reason = validate_sql("SELECT * FROM pg_catalog.pg_tables").reason

        assert "pg_catalog.pg_tables" in reason
        assert "Database Explorer" in reason

    def test_catalog_access_can_be_enabled_explicitly(self, monkeypatch):
        monkeypatch.setenv("SQL_COPILOT_ALLOW_SYSTEM_CATALOGS", "1")

        assert validate_sql("SELECT * FROM pg_catalog.pg_tables").allowed

    def test_dangerous_functions_stay_blocked_even_with_catalogs_enabled(self, monkeypatch):
        monkeypatch.setenv("SQL_COPILOT_ALLOW_SYSTEM_CATALOGS", "1")

        assert not validate_sql("SELECT pg_advisory_lock(1)").allowed


class TestAuditModule:

    def test_writes_json_lines(self, audit_log):
        audit("answer", question="how many?", sql="SELECT 1", rows=3)
        audit("index", ok=True)

        written = events(audit_log)

        assert [e["event"] for e in written] == ["answer", "index"]
        assert written[0]["question"] == "how many?"
        assert written[0]["rows"] == 3
        assert "ts" in written[0]

    def test_secrets_and_none_values_are_never_written(self, audit_log):
        audit("connection", password="hunter2", api_key="sk-secret", token="t", name="Prod", host=None)

        [record] = events(audit_log)

        assert "hunter2" not in audit_log.read_text()
        assert "sk-secret" not in audit_log.read_text()
        assert "host" not in record
        assert record["name"] == "Prod"

    def test_long_values_are_truncated(self, audit_log):
        audit("answer", question="q" * 5000, sql="s" * 9000, error="e" * 2000)

        [record] = events(audit_log)

        assert len(record["question"]) == 1000
        assert len(record["sql"]) == 4000
        assert len(record["error"]) == 500

    def test_lists_are_capped_and_unicode_kept(self, audit_log):
        audit("answer", tables=[f"t{i}" for i in range(200)], question="café ☕")

        [record] = events(audit_log)

        assert len(record["tables"]) == 50
        assert record["question"] == "café ☕"

    def test_can_be_turned_off(self, audit_log, monkeypatch):
        monkeypatch.setenv("SQL_COPILOT_AUDIT_LOG", "off")
        configure_logging(force=True)

        audit("answer", question="q")

        file_handlers = [
            h for h in logging.getLogger("sql_copilot.audit").handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert audit_path() is None
        assert file_handlers == []
        assert "answer" not in (audit_log.read_text() if audit_log.exists() else "")

    @pytest.mark.parametrize("value", ["", "off", "OFF", "false", "0", "none"])
    def test_off_spellings(self, monkeypatch, value):
        monkeypatch.setenv("SQL_COPILOT_AUDIT_LOG", value)

        assert audit_path() is None

    def test_default_path_is_inside_the_data_folder(self, monkeypatch):
        monkeypatch.delenv("SQL_COPILOT_AUDIT_LOG")

        assert audit_path().parts[-2:] == ("data", "audit.log")

    def test_unwritable_location_does_not_crash(self, tmp_path, monkeypatch, caplog):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setenv("SQL_COPILOT_AUDIT_LOG", str(blocker / "sub" / "audit.log"))

        configure_logging(force=True)
        audit("answer", question="q")

    def test_audit_never_raises(self, audit_log, monkeypatch):
        monkeypatch.setattr(audit_module.json, "dumps", lambda *a, **k: 1 / 0)

        audit("answer", question="q")

    def test_configuration_is_idempotent(self, audit_log):
        configure_logging()
        configure_logging()
        audit("index")

        assert len(events(audit_log)) == 1
        file_handlers = [
            h for h in logging.getLogger("sql_copilot.audit").handlers
            if isinstance(h, RotatingFileHandler)
        ]

        assert len(file_handlers) == 1

    def test_audit_lines_do_not_go_to_the_console(self, audit_log, capsys):
        audit("answer", question="visible?")

        assert "visible?" not in capsys.readouterr().err


class TestAppWritesTheAuditTrail:
    """Uses the real app through the harness from test_app.py."""

    @pytest.fixture
    def ui(self, env, monkeypatch):
        from test_app import Harness

        return Harness(monkeypatch, "SELECT name FROM customer ORDER BY id")

    def test_indexing_is_recorded(self, ui, audit_log):
        ui.index()

        [record] = [e for e in events(audit_log) if e["event"] == "index"]

        assert record["ok"] is True
        assert record["datasource"] == "Shop"
        assert record["dialect"] == "sqlite"
        assert record["tables"] == 4 and record["views"] == 1
        assert record["examples"] == 4
        assert record["elapsed_ms"] >= 0

    def test_failed_indexing_is_recorded(self, ui, audit_log, monkeypatch):
        monkeypatch.setattr("database.database_inspector.inspect_database", lambda *a, **k: 1 / 0)

        ui.button("Discover & index database").click().run()

        record = [e for e in events(audit_log) if e["event"] == "index"][-1]

        assert record["ok"] is False
        assert "division" in record["error"]

    def test_answer_has_sql_outcome_and_tokens_but_no_rows(self, ui, audit_log):
        ui.index()
        ui.ask("list customers")

        [record] = [e for e in events(audit_log) if e["event"] == "answer"]

        assert record["question"] == "list customers"
        assert record["sql"] == "SELECT name FROM customer ORDER BY id"
        assert record["outcome"] == "ok"
        assert record["rows"] == 2
        assert record["input_tokens"] > 0
        assert record["provider"] and record["model"]
        assert "Ann" not in audit_log.read_text()
        assert "Bob" not in audit_log.read_text()

    @pytest.mark.parametrize(
        "sql,outcome",
        [
            ("DELETE FROM customer", "blocked"),
            ("SELECT load_extension('x')", "rejected"),
            ("SELECT nope FROM customer", "error"),
            ("CANNOT_GENERATE_SQL", "no_sql"),
            ("CLARIFY: which one?", "clarify"),
        ],
    )
    def test_outcomes(self, env, monkeypatch, audit_log, sql, outcome):
        from test_app import Harness

        ui = Harness(monkeypatch, sql).index()
        ui.ask("something")

        record = [e for e in events(audit_log) if e["event"] == "answer"][-1]

        assert record["outcome"] == outcome

    def test_answer_shows_how_long_it_took(self, ui):
        ui.index()

        assert ui.ask("list customers")["elapsed_ms"] >= 0

    def test_examples_approvals_and_connections_are_recorded(self, ui, audit_log, shop_db):
        ui.index()
        ui.button(sidebar=False, label="How many rows are in orders?").click().run()
        ui.button(sidebar=False, label="Correct").click().run()
        ui.widget("selectbox", key="conn_type").select("sqlite").run()
        ui.widget("text_input", key="conn_db").set_value(shop_db).run()
        ui.widget("text_input", key="conn_name").set_value("Copy").run()
        ui.button(key="conn_test").click().run()
        ui.button(key="conn_add").click().run()

        by_event = {}
        for record in events(audit_log):
            by_event.setdefault(record["event"], []).append(record)

        assert by_event["example"][0]["outcome"] == "ok"
        assert by_event["example"][0]["sql"].startswith("SELECT COUNT(*)")
        assert by_event["approve"][0]["question"] == "How many rows are in orders?"
        assert [c["action"] for c in by_event["connection"]] == ["test", "added"]
        assert by_event["connection"][0]["ok"] is True

    def test_passwords_never_reach_the_log(self, ui, audit_log):
        ui.widget("selectbox", key="conn_type").select("mysql").run()
        ui.widget("text_input", key="conn_name").set_value("Remote").run()
        ui.widget("text_input", key="conn_db").set_value("app").run()
        ui.widget("text_input", key="conn_user").set_value("reader").run()
        ui.widget("text_input", key="conn_password").set_value("TOP-SECRET-PW").run()
        ui.button(key="conn_add").click().run()

        text = audit_log.read_text()

        assert '"added"' in text
        assert "TOP-SECRET-PW" not in text

    def test_privacy_changes_are_recorded(self, ui, audit_log):
        ui.index()
        ui.widget("multiselect", label="Exclude tables").select("main.customer").run()
        ui.button("Apply privacy settings").click().run()

        record = [e for e in events(audit_log) if e["event"] == "privacy"][-1]

        assert record["excluded_tables"] == 1


class TestFailuresAreLoggedNotSwallowed:

    @pytest.fixture
    def ui(self, env, monkeypatch):
        from test_app import Harness

        return Harness(monkeypatch, "SELECT nope FROM customer")

    def test_failed_repair_is_logged(self, ui, caplog):
        ui.index()

        with caplog.at_level(logging.WARNING, logger="sql_copilot"):
            caplog.handler.setLevel(logging.WARNING)
            logging.getLogger("sql_copilot").addHandler(caplog.handler)
            ui.queue[:] = ["SELECT nope FROM customer", "SELECT broken FROM"]
            ui.ask("list customers")

        assert any("repair" in r.getMessage().lower() for r in caplog.records)

    def test_model_error_is_logged_and_shown(self, ui, caplog, monkeypatch):
        ui.index()
        monkeypatch.setattr("services.llm_service.get_llm", lambda **k: (_ for _ in ()).throw(RuntimeError("model gone")))
        logging.getLogger("sql_copilot").addHandler(caplog.handler)

        reply = ui.ask("anything")

        assert "model gone" in reply["error"]
        assert any("Answer failed" in r.getMessage() for r in caplog.records)


@pytest.mark.integration
def test_large_result_does_not_load_into_memory():
    """Streaming: asking for 20M rows must stay within a small memory budget."""

    import os
    import resource

    if not os.getenv("SQL_COPILOT_PG_TEST"):
        pytest.skip("set SQL_COPILOT_PG_TEST=1 to run PostgreSQL tests")

    from config.datasource_config import DATASOURCES
    from database.dialects import is_configured
    from database.query_executor import execute_query

    name = next((n for n, c in DATASOURCES.items() if is_configured(c)), None)
    if not name:
        pytest.skip("no configured PostgreSQL datasource")

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = execute_query(name, "SELECT g, md5(g::text) AS h FROM generate_series(1, 20000000) g")
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1e6

    assert result.error is None
    assert result.row_count == 500
    assert grown_mb < 60
