from sqlalchemy import text

from database.connection_manager import get_connection
from database.schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    SchemaMetadata,
    TableMetadata, infer_relationships,
)


def inspect_database(datasource_name: str) -> DatabaseMetadata:

    columns_sql = text(
        """
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable,
            column_default,
            ordinal_position
        FROM information_schema.columns
        WHERE table_schema NOT IN (
            'pg_catalog',
            'information_schema'
        )
        ORDER BY
            table_schema,
            table_name,
            ordinal_position
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

    views_sql = text(
        """
        SELECT COUNT(*)
        FROM information_schema.views
        WHERE table_schema NOT IN (
            'pg_catalog',
            'information_schema'
        )
        """
    )

    with get_connection(datasource_name) as connection:

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

        view_count = connection.execute(
            views_sql
        ).scalar_one()

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
            )
        )

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
        view_count=int(view_count),
    )

    infer_relationships(database_metadata)

    return database_metadata