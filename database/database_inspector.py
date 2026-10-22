from sqlalchemy import text
import os

from config.datasource_config import DATASOURCES
from database.connection_manager import get_connection
from database.dialects import get_dialect
from database.generic_inspector import inspect_generic
from database.schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    SchemaMetadata,
    TableMetadata, infer_relationships, infer_shared_key_relationships,
)
from flyway.flyway_inspector import build_flyway_object_state, inspect_flyway_directory


RELKIND_NAMES = {
    "r": "table",
    "p": "table",
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
}

TEXT_TYPE_PREFIXES = ("text", "character", "boolean")

MAX_DISTINCT_VALUES = 15
MAX_VALUES_PER_COLUMN = 10
MAX_VALUE_LENGTH = 40


def _inspect_postgres(
    datasource_name: str,
    config: dict | None = None,
) -> DatabaseMetadata:

    columns_sql = text(
        """
        SELECT
            n.nspname AS table_schema,
            c.relname AS table_name,
            c.relkind AS relkind,
            obj_description(c.oid, 'pg_class')
                AS table_comment,
            c.reltuples::bigint AS approx_rows,
            a.attname AS column_name,
            format_type(a.atttypid, a.atttypmod)
                AS data_type,
            CASE WHEN a.attnotnull
                THEN 'NO' ELSE 'YES'
            END AS is_nullable,
            pg_get_expr(d.adbin, d.adrelid)
                AS column_default,
            col_description(c.oid, a.attnum)
                AS column_comment
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        JOIN pg_attribute a
            ON a.attrelid = c.oid
           AND a.attnum > 0
           AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d
            ON d.adrelid = c.oid
           AND d.adnum = a.attnum
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN (
              'pg_catalog',
              'information_schema',
              'pg_toast'
          )
          AND n.nspname NOT LIKE 'pg\\_temp\\_%'
          AND NOT c.relispartition
          AND has_table_privilege(c.oid, 'SELECT')
        ORDER BY
            n.nspname,
            c.relname,
            a.attnum
        """
    )

    enums_sql = text(
        """
        SELECT
            n.nspname AS table_schema,
            c.relname AS table_name,
            a.attname AS column_name,
            array_agg(
                e.enumlabel::text
                ORDER BY e.enumsortorder
            ) AS labels
        FROM pg_attribute a
        JOIN pg_class c
            ON c.oid = a.attrelid
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        JOIN pg_enum e
            ON e.enumtypid = a.atttypid
        WHERE a.attnum > 0
          AND NOT a.attisdropped
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN (
              'pg_catalog',
              'information_schema'
          )
        GROUP BY n.nspname, c.relname, a.attname
        """
    )

    common_values_sql = text(
        """
        SELECT
            schemaname AS table_schema,
            tablename AS table_name,
            attname AS column_name,
            most_common_vals::text::text[] AS vals
        FROM pg_stats
        WHERE schemaname NOT IN (
            'pg_catalog',
            'information_schema'
        )
          AND n_distinct > 0
          AND n_distinct <= :max_distinct
          AND most_common_vals IS NOT NULL
        """
    )

    primary_keys_sql = text(
        """
        SELECT
            n.nspname AS table_schema,
            c.relname AS table_name,
            a.attname AS column_name
        FROM pg_index i
        JOIN pg_class c
            ON c.oid = i.indrelid
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        JOIN pg_attribute a
            ON a.attrelid = c.oid
           AND a.attnum = ANY(i.indkey)
        WHERE i.indisprimary
          AND n.nspname NOT IN (
              'pg_catalog',
              'information_schema'
          )
        """
    )

    foreign_keys_sql = text(
        """
        SELECT
            ns.nspname AS table_schema,
            cl.relname AS table_name,
            att.attname AS column_name,
            fns.nspname AS foreign_schema,
            fcl.relname AS foreign_table,
            fatt.attname AS foreign_column
        FROM pg_constraint con
        JOIN pg_class cl
            ON cl.oid = con.conrelid
        JOIN pg_namespace ns
            ON ns.oid = cl.relnamespace
        JOIN pg_class fcl
            ON fcl.oid = con.confrelid
        JOIN pg_namespace fns
            ON fns.oid = fcl.relnamespace
        JOIN LATERAL
            unnest(con.conkey)
            WITH ORDINALITY AS cols(attnum, ord)
            ON TRUE
        JOIN LATERAL
            unnest(con.confkey)
            WITH ORDINALITY AS fcols(attnum, ord)
            ON fcols.ord = cols.ord
        JOIN pg_attribute att
            ON att.attrelid = cl.oid
           AND att.attnum = cols.attnum
        JOIN pg_attribute fatt
            ON fatt.attrelid = fcl.oid
           AND fatt.attnum = fcols.attnum
        WHERE con.contype = 'f'
          AND ns.nspname NOT IN (
              'pg_catalog',
              'information_schema'
          )
        """
    )

    indexes_sql = text(
        """
        SELECT
            n.nspname AS table_schema,
            t.relname AS table_name,
            i.relname AS index_name,
            ix.indisunique AS is_unique,
            am.amname AS method,
            pg_get_indexdef(ix.indexrelid) AS index_definition,
            array_agg(
                a.attname
                ORDER BY k.ord
            ) FILTER (
                WHERE a.attname IS NOT NULL
            ) AS columns
        FROM pg_index ix
        JOIN pg_class t
            ON t.oid = ix.indrelid
        JOIN pg_class i
            ON i.oid = ix.indexrelid
        JOIN pg_namespace n
            ON n.oid = t.relnamespace
        JOIN pg_am am
            ON am.oid = i.relam
        JOIN LATERAL
            unnest(ix.indkey)
            WITH ORDINALITY AS k(attnum, ord)
            ON TRUE
        LEFT JOIN pg_attribute a
            ON a.attrelid = t.oid
           AND a.attnum = k.attnum
        WHERE n.nspname NOT IN (
            'pg_catalog',
            'information_schema'
        )
        GROUP BY
            n.nspname,
            t.relname,
            i.relname,
            ix.indisunique,
            am.amname,
            ix.indexrelid
        ORDER BY
            n.nspname,
            t.relname,
            i.relname
        """
    )

    with get_connection(datasource_name, config) as connection:

        columns = (
            connection
            .execute(columns_sql)
            .mappings()
            .all()
        )

        primary_keys = (
            connection
            .execute(primary_keys_sql)
            .mappings()
            .all()
        )

        foreign_keys = (
            connection
            .execute(foreign_keys_sql)
            .mappings()
            .all()
        )

        indexes = (
            connection
            .execute(indexes_sql)
            .mappings()
            .all()
        )

        enums = (
            connection
            .execute(enums_sql)
            .mappings()
            .all()
        )

        common_values = (
            connection
            .execute(
                common_values_sql,
                {"max_distinct": MAX_DISTINCT_VALUES},
            )
            .mappings()
            .all()
        )

    primary_key_set = {
        (
            row["table_schema"],
            row["table_name"],
            row["column_name"],
        )
        for row in primary_keys
    }

    tables = {}

    # ---------------------------------------------------------
    # Tables + Columns
    # ---------------------------------------------------------

    for row in columns:

        key = (
            row["table_schema"],
            row["table_name"],
        )

        if key not in tables:
            tables[key] = TableMetadata(
                schema=row["table_schema"],
                name=row["table_name"],
                kind=RELKIND_NAMES.get(
                    row["relkind"],
                    "table",
                ),
                comment=row["table_comment"],
                approx_rows=row["approx_rows"],
            )

        tables[key].columns.append(
            ColumnMetadata(
                name=row["column_name"],
                data_type=row["data_type"],
                nullable=row["is_nullable"],
                default=row["column_default"],
                primary_key=(
                    row["table_schema"],
                    row["table_name"],
                    row["column_name"],
                )
                in primary_key_set,
                comment=row["column_comment"],
            )
        )

    # ---------------------------------------------------------
    # Enum labels + common values
    # ---------------------------------------------------------

    column_lookup = {
        (key[0], key[1], column.name): column
        for key, table in tables.items()
        for column in table.columns
    }

    for row in common_values:

        column = column_lookup.get(
            (
                row["table_schema"],
                row["table_name"],
                row["column_name"],
            )
        )

        if column is None or not column.data_type.startswith(TEXT_TYPE_PREFIXES):
            continue

        column.values = [
            str(value)[:MAX_VALUE_LENGTH]
            for value in (row["vals"] or [])
            if value is not None
        ][:MAX_VALUES_PER_COLUMN]

        column.values_kind = "common"

    for row in enums:

        column = column_lookup.get(
            (
                row["table_schema"],
                row["table_name"],
                row["column_name"],
            )
        )

        if column is None:
            continue

        column.values = list(row["labels"])
        column.values_kind = "enum"

    # ---------------------------------------------------------
    # Foreign Keys
    # ---------------------------------------------------------

    for row in foreign_keys:

        key = (
            row["table_schema"],
            row["table_name"],
        )

        if key in tables:

            tables[key].foreign_keys.append(
                ForeignKeyMetadata(
                    column=row["column_name"],
                    foreign_schema=row["foreign_schema"],
                    foreign_table=row["foreign_table"],
                    foreign_column=row["foreign_column"],
                )
            )

    # ---------------------------------------------------------
    # Indexes
    # ---------------------------------------------------------

    for row in indexes:

        key = (
            row["table_schema"],
            row["table_name"],
        )

        if key not in tables:
            continue

        index_columns = [
            str(column)
            for column in (row["columns"] or [])
            if column is not None
        ]

        tables[key].indexes.append(
            IndexMetadata(
                name=row["index_name"],
                columns=index_columns,
                unique=row["is_unique"],
                method=row["method"],
                definition=row["index_definition"],
            )
        )

    # ---------------------------------------------------------
    # Group Tables By Schema
    # ---------------------------------------------------------

    schemas = {}

    for table in tables.values():

        if table.schema not in schemas:
            schemas[table.schema] = SchemaMetadata(
                name=table.schema
            )

        schemas[table.schema].tables.append(table)

    # ---------------------------------------------------------
    # Return Database Metadata
    # ---------------------------------------------------------

    database_metadata = DatabaseMetadata(
        schemas=sorted(
            schemas.values(),
            key=lambda schema: schema.name,
        ),
    )

    return database_metadata


def inspect_database(
    datasource_name: str,
    config: dict | None = None,
) -> DatabaseMetadata:

    config = config or DATASOURCES[datasource_name]

    if get_dialect(config).key == "postgresql":

        database_metadata = _inspect_postgres(datasource_name, config)

    else:

        database_metadata = inspect_generic(datasource_name, config)

    flyway_path = os.getenv("FLYWAY_MIGRATION_PATH")

    if flyway_path:

        database_metadata = apply_flyway_state(
            database_metadata,
            flyway_path,
        )

    infer_relationships(database_metadata)
    infer_shared_key_relationships(database_metadata)

    return database_metadata


def apply_flyway_state(
    database_metadata: DatabaseMetadata,
    migration_directory: str,
) -> DatabaseMetadata:

    migrations = inspect_flyway_directory(
        migration_directory
    )

    flyway_state = build_flyway_object_state(
        migrations
    )

    for schema in database_metadata.schemas:

        for table in schema.tables:

            key = (
                f"{schema.name}."
                f"{table.name}"
            )

            state = flyway_state.get(key)

            if state is None:
                continue

            table.flyway_managed = True
            table.flyway_active = state.exists
            table.flyway_created_by = (
                state.created_by
            )
            table.flyway_dropped_by = (
                state.dropped_by
            )
            table.flyway_last_migration = (
                state.last_migration
            )

    return database_metadata