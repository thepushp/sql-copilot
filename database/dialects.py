import os
from dataclasses import dataclass, field

from sqlalchemy.engine import URL


@dataclass(frozen=True)
class Dialect:
    key: str
    label: str
    sqlglot: str
    scheme: str
    default_port: int | None
    database_label: str = "Database"
    needs_host: bool = True
    system_schemas: frozenset = frozenset()
    # (statement, required); {ms} and {s} are the statement timeout
    setup: tuple = ()
    forbidden_functions: frozenset = frozenset()
    limit_hint: str = "add LIMIT 50"
    notes: str = ""
    read_only_warning: str = ""
    supports_ssl_mode: bool = False
    connect_args: dict = field(default_factory=dict)
    forbidden_prefixes: tuple = ()
    system_table_prefixes: tuple = ()
    system_catalogs: frozenset = frozenset()


DIALECTS = {
    "postgresql": Dialect(
        key="postgresql",
        label="PostgreSQL",
        sqlglot="postgres",
        scheme="postgresql+psycopg",
        default_port=5432,
        system_schemas=frozenset(
            {"pg_catalog", "information_schema", "pg_toast"}
        ),
        setup=(
            ("SET TRANSACTION READ ONLY", True),
            ("SET LOCAL statement_timeout = {ms}", True),
        ),
        forbidden_functions=frozenset(
            {
                "pg_sleep",
                "pg_sleep_for",
                "pg_sleep_until",
                "pg_read_file",
                "pg_read_binary_file",
                "pg_ls_dir",
                "pg_terminate_backend",
                "pg_cancel_backend",
                "set_config",
                "dblink",
                "dblink_exec",
                "lo_import",
                "lo_export",
                "lo_unlink",
                "query_to_xml",
                "pg_notify",
                "pg_reload_conf",
                "pg_rotate_logfile",
                "pg_switch_wal",
                "pg_create_restore_point",
                "pg_logical_emit_message",
            }
        ),
        limit_hint="add LIMIT 50",
        notes=(
            "Use double quotes only for identifiers that need them. "
            "Date arithmetic example: current_date - interval '7 days'."
        ),
        supports_ssl_mode=True,
        connect_args={"connect_timeout": 10},
        forbidden_prefixes=(
            "pg_advisory",
            "pg_try_advisory",
            "pg_stat_reset",
            "lo_",
        ),
        system_table_prefixes=("pg_",),
    ),
    "mysql": Dialect(
        key="mysql",
        label="MySQL / MariaDB",
        sqlglot="mysql",
        scheme="mysql+pymysql",
        default_port=3306,
        database_label="Database (schema)",
        system_schemas=frozenset(
            {"information_schema", "mysql", "performance_schema", "sys"}
        ),
        setup=(
            ("SET TRANSACTION READ ONLY", True),
            ("SET SESSION MAX_EXECUTION_TIME = {ms}", False),
            ("SET SESSION max_statement_time = {s}", False),
        ),
        forbidden_functions=frozenset(
            {
                "sleep",
                "benchmark",
                "load_file",
                "get_lock",
                "release_lock",
                "release_all_locks",
                "master_pos_wait",
                "sys_exec",
                "sys_eval",
            }
        ),
        limit_hint="add LIMIT 50",
        notes=(
            "Use backticks for identifiers that need quoting. Tables live "
            "in the database named in the context; qualify as "
            "database.table only when needed. Date arithmetic example: "
            "DATE_SUB(CURDATE(), INTERVAL 7 DAY)."
        ),
        connect_args={"connect_timeout": 10},
    ),
    "mssql": Dialect(
        key="mssql",
        label="SQL Server",
        sqlglot="tsql",
        scheme="mssql+pymssql",
        default_port=1433,
        system_schemas=frozenset(
            {
                "information_schema",
                "sys",
                "guest",
                "db_owner",
                "db_accessadmin",
                "db_securityadmin",
                "db_ddladmin",
                "db_backupoperator",
                "db_datareader",
                "db_datawriter",
                "db_denydatareader",
                "db_denydatawriter",
            }
        ),
        setup=(("SET LOCK_TIMEOUT {ms}", False),),
        forbidden_functions=frozenset(
            {
                "xp_cmdshell",
                "openrowset",
                "openquery",
                "opendatasource",
                "sp_executesql",
                "sp_oacreate",
            }
        ),
        limit_hint=(
            "use SELECT TOP 50 (or OFFSET ... FETCH NEXT 50 ROWS ONLY); "
            "never use LIMIT"
        ),
        notes=(
            "Use square brackets for identifiers that need quoting. "
            "Qualify tables as schema.table. Date arithmetic example: "
            "DATEADD(day, -7, GETDATE())."
        ),
        read_only_warning=(
            "SQL Server has no read-only transaction mode. Use a login "
            "that only has SELECT permission."
        ),
        connect_args={"login_timeout": 10},
        forbidden_prefixes=("xp_", "sp_"),
        system_catalogs=frozenset({"master", "msdb", "tempdb", "model"}),
    ),
    "sqlite": Dialect(
        key="sqlite",
        label="SQLite",
        sqlglot="sqlite",
        scheme="sqlite",
        default_port=None,
        database_label="Database file path",
        needs_host=False,
        system_schemas=frozenset({"temp"}),
        setup=(("PRAGMA query_only = ON", True),),
        forbidden_functions=frozenset(
            {"load_extension", "readfile", "writefile", "edit"}
        ),
        limit_hint="add LIMIT 50",
        notes=(
            "There are no schemas: refer to tables by plain name. Date "
            "arithmetic example: date('now', '-7 day')."
        ),
        system_table_prefixes=("sqlite_",),
    ),
    "oracle": Dialect(
        key="oracle",
        label="Oracle",
        sqlglot="oracle",
        scheme="oracle+oracledb",
        default_port=1521,
        database_label="Service name",
        system_schemas=frozenset(
            {
                "SYS",
                "SYSTEM",
                "OUTLN",
                "XDB",
                "CTXSYS",
                "MDSYS",
                "ORDSYS",
                "DBSNMP",
                "APPQOSSYS",
                "AUDSYS",
                "GSMADMIN_INTERNAL",
                "LBACSYS",
                "OJVMSYS",
                "WMSYS",
                "DVSYS",
            }
        ),
        setup=(("SET TRANSACTION READ ONLY", True),),
        forbidden_functions=frozenset(
            {"sleep", "utl_http", "utl_file", "utl_smtp", "utl_inaddr"}
        ),
        limit_hint=(
            "use FETCH FIRST 50 ROWS ONLY; never use LIMIT or TOP"
        ),
        notes=(
            "Unquoted identifiers are uppercase. Do not end the statement "
            "with a semicolon. Date arithmetic example: SYSDATE - 7."
        ),
        connect_args={"tcp_connect_timeout": 10},
        forbidden_prefixes=("dbms_", "utl_"),
        system_table_prefixes=("dba_", "v$", "gv$"),
    ),
}

DEFAULT_DIALECT = "postgresql"


def get_dialect(config_or_key=None) -> Dialect:

    if isinstance(config_or_key, dict):
        key = config_or_key.get("driver") or DEFAULT_DIALECT
    else:
        key = config_or_key or DEFAULT_DIALECT

    return DIALECTS.get(key, DIALECTS[DEFAULT_DIALECT])


def default_port(dialect_key: str):

    return get_dialect(dialect_key).default_port


def is_configured(config) -> bool:

    if not config:
        return False

    dialect = get_dialect(config)

    if not config.get("database"):
        return False

    if dialect.needs_host:
        return bool(config.get("host") and config.get("username"))

    return True


def build_url(config) -> URL:

    dialect = get_dialect(config)

    if dialect.key == "sqlite":

        path = os.path.expanduser(config["database"])

        if not os.path.isfile(path):
            raise FileNotFoundError(f"SQLite file not found: {path}")

        return URL.create(
            "sqlite",
            database=f"file:{path}",
            query={"mode": "ro", "uri": "true"},
        )

    query = {}

    if dialect.supports_ssl_mode and config.get("sslmode"):
        query["sslmode"] = config["sslmode"]

    database = config["database"]

    if dialect.key == "oracle":
        query["service_name"] = database
        database = None

    return URL.create(
        dialect.scheme,
        username=config["username"],
        password=config.get("password") or None,
        host=config["host"],
        port=config.get("port") or dialect.default_port,
        database=database,
        query=query,
    )
