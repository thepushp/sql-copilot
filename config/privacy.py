import copy
import fnmatch
from dataclasses import dataclass, field

from database.schema_metadata import DatabaseMetadata


DEFAULT_COLUMN_PATTERNS = [
    "*password*",
    "*passwd*",
    "*secret*",
    "*token*",
    "*ssn*",
    "*api_key*",
    "*apikey*",
]


INFRASTRUCTURE_TABLE_PATTERNS = [
    "*schema_history*",
    "flyway_*",
    "shedlock",
    "pg_stat_*",
    "qrtz_*",
    "databasechangelog*",
    "alembic_version",
    "django_migrations",
    "spring_session*",
]


@dataclass
class PrivacySettings:
    excluded_schemas: list[str] = field(default_factory=list)
    excluded_tables: list[str] = field(default_factory=list)
    column_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_COLUMN_PATTERNS)
    )
    include_sample_values: bool = True
    hide_infrastructure: bool = True


@dataclass
class AccessPolicy:
    blocked_tables: set[str] = field(default_factory=set)
    blocked_columns: set[str] = field(default_factory=set)
    star_restricted: set[str] = field(default_factory=set)


def _column_blocked(name, patterns):

    lowered = name.lower()

    return any(
        fnmatch.fnmatch(lowered, pattern.lower())
        for pattern in patterns
    )


def apply_privacy(
    metadata: DatabaseMetadata,
    settings: PrivacySettings,
) -> tuple[DatabaseMetadata, AccessPolicy]:

    filtered = copy.deepcopy(metadata)

    policy = AccessPolicy()

    excluded_schemas = {s.lower() for s in settings.excluded_schemas}
    excluded_tables = {t.lower() for t in settings.excluded_tables}
    patterns = [p for p in settings.column_patterns if p.strip()]

    for schema in filtered.schemas:

        kept = []

        for table in schema.tables:

            qualified = f"{table.schema}.{table.name}".lower()

            if (
                table.schema.lower() in excluded_schemas
                or qualified in excluded_tables
            ):

                policy.blocked_tables.add(qualified)

                continue

            if settings.hide_infrastructure and any(
                fnmatch.fnmatch(table.name.lower(), pattern)
                for pattern in INFRASTRUCTURE_TABLE_PATTERNS
            ):

                continue

            hidden = {
                column.name
                for column in table.columns
                if _column_blocked(column.name, patterns)
            }

            if hidden:

                policy.blocked_columns.update(
                    name.lower() for name in hidden
                )

                policy.star_restricted.add(qualified)

                table.columns = [
                    column
                    for column in table.columns
                    if column.name not in hidden
                ]

                table.indexes = [
                    index
                    for index in table.indexes
                    if not hidden.intersection(index.columns)
                ]

            if not settings.include_sample_values:

                for column in table.columns:

                    if column.values_kind == "common":

                        column.values = []
                        column.values_kind = ""

            kept.append(table)

        schema.tables = kept

    filtered.schemas = [
        schema for schema in filtered.schemas if schema.tables
    ]

    live = {
        f"{table.schema}.{table.name}".lower()
        for schema in filtered.schemas
        for table in schema.tables
    }

    for schema in filtered.schemas:

        for table in schema.tables:

            table.foreign_keys = [
                fk
                for fk in table.foreign_keys
                if (
                    f"{fk.foreign_schema}.{fk.foreign_table}".lower()
                    in live
                    and not _column_blocked(fk.column, patterns)
                    and not _column_blocked(fk.foreign_column, patterns)
                )
            ]

    return filtered, policy
