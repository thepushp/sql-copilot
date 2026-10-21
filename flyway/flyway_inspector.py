from pathlib import Path
import re

from flyway.migration_metadata import (
    FlywayObjectState,
    MigrationMetadata,
    MigrationOperation,
)


CREATE_TABLE_PATTERN = re.compile(
    r"""
    CREATE\s+
    (?:TABLE|TABLE\s+IF\s+NOT\s+EXISTS)
    \s+
    (?:(?P<schema>[A-Za-z0-9_]+)\.)?
    ["`]?(?P<table>[A-Za-z0-9_]+)["`]?
    """,
    re.IGNORECASE | re.VERBOSE,
)


DROP_TABLE_PATTERN = re.compile(
    r"""
    DROP\s+
    TABLE
    \s+
    (?:IF\s+EXISTS\s+)?
    (?:(?P<schema>[A-Za-z0-9_]+)\.)?
    ["`]?(?P<table>[A-Za-z0-9_]+)["`]?
    """,
    re.IGNORECASE | re.VERBOSE,
)


ALTER_TABLE_PATTERN = re.compile(
    r"""
    ALTER\s+
    TABLE
    \s+
    (?:(?P<schema>[A-Za-z0-9_]+)\.)?
    ["`]?(?P<table>[A-Za-z0-9_]+)["`]?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_migration_filename(filename: str):
    """
    Supports standard Flyway names such as:

        V001__create_student.sql
        V120__create_student_equipment.sql
        V145__drop_student_equipment.sql
        R__some_repeatable_migration.sql
    """

    name = Path(filename).name

    match = re.match(
        r"V(?P<version>[0-9._]+)__(?P<description>.+)\.sql$",
        name,
        re.IGNORECASE,
    )

    if match:
        return (
            match.group("version"),
            match.group("description").replace("_", " "),
        )

    repeatable = re.match(
        r"R__(?P<description>.+)\.sql$",
        name,
        re.IGNORECASE,
    )

    if repeatable:
        return (
            "R",
            repeatable.group("description").replace("_", " "),
        )

    return (
        "UNKNOWN",
        name,
    )


def _detect_operation(sql: str) -> MigrationOperation:

    if CREATE_TABLE_PATTERN.search(sql):
        return MigrationOperation.CREATE

    if DROP_TABLE_PATTERN.search(sql):
        return MigrationOperation.DROP

    if ALTER_TABLE_PATTERN.search(sql):
        return MigrationOperation.ALTER

    return MigrationOperation.UNKNOWN


def _extract_tables(sql: str) -> list[str]:

    tables = set()

    for pattern in (
        CREATE_TABLE_PATTERN,
        DROP_TABLE_PATTERN,
        ALTER_TABLE_PATTERN,
    ):

        for match in pattern.finditer(sql):

            schema = match.group("schema") or "public"
            table = match.group("table")

            tables.add(
                f"{schema}.{table}"
            )

    return sorted(tables)


def inspect_flyway_directory(
    migration_directory: str,
) -> list[MigrationMetadata]:

    directory = Path(migration_directory)

    if not directory.exists():
        raise FileNotFoundError(
            f"Flyway migration directory not found: "
            f"{migration_directory}"
        )

    migrations = []

    for file in sorted(directory.glob("*.sql")):

        version, description = parse_migration_filename(
            file.name
        )

        sql = file.read_text(
            encoding="utf-8"
        )

        operation = _detect_operation(sql)

        objects = _extract_tables(sql)

        migrations.append(
            MigrationMetadata(
                version=version,
                description=description,
                filename=file.name,
                operation=operation,
                objects=objects,
            )
        )

    return migrations


def build_flyway_object_state(
    migrations: list[MigrationMetadata],
) -> dict[str, FlywayObjectState]:

    state: dict[str, FlywayObjectState] = {}

    for migration in migrations:

        for object_name in migration.objects:

            schema, table = object_name.split(
                ".",
                1,
            )

            key = f"{schema}.{table}"

            if key not in state:
                state[key] = FlywayObjectState(
                    schema=schema,
                    table=table,
                )

            current = state[key]

            if migration.operation == MigrationOperation.CREATE:

                current.exists = True
                current.created_by = migration.filename
                current.dropped_by = None
                current.last_migration = migration.filename

            elif migration.operation == MigrationOperation.DROP:

                current.exists = False
                current.dropped_by = migration.filename
                current.last_migration = migration.filename

            elif migration.operation == MigrationOperation.ALTER:

                current.last_migration = migration.filename

    return state