from langchain_core.documents import Document
from sqlalchemy import text

from config.datasource_config import DATASOURCES
from database.connection_manager import (
    get_connection,
)


def get_schema_documents(
    datasource_name: str,
):

    schema_name = DATASOURCES[
        datasource_name
    ]["schema"]

    columns_sql = text(
        """
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable,
            ordinal_position
        FROM information_schema.columns
        WHERE table_schema = :schema_name
        ORDER BY
            table_name,
            ordinal_position
        """
    )

    constraints_sql = text(
        """
        SELECT
            tc.table_name,
            tc.constraint_type,
            kcu.column_name,
            ccu.table_name
                AS foreign_table_name,
            ccu.column_name
                AS foreign_column_name
        FROM information_schema.table_constraints tc

        LEFT JOIN
            information_schema.key_column_usage kcu
            ON tc.constraint_name =
               kcu.constraint_name
            AND tc.table_schema =
                kcu.table_schema

        LEFT JOIN
            information_schema.constraint_column_usage ccu
            ON tc.constraint_name =
               ccu.constraint_name
            AND tc.table_schema =
                ccu.table_schema

        WHERE tc.table_schema = :schema_name

        ORDER BY tc.table_name
        """
    )

    with get_connection(
        datasource_name
    ) as connection:

        columns = connection.execute(
            columns_sql,
            {
                "schema_name": schema_name
            },
        ).mappings().all()

        constraints = connection.execute(
            constraints_sql,
            {
                "schema_name": schema_name
            },
        ).mappings().all()

    tables = {}

    for row in columns:

        tables.setdefault(
            row["table_name"],
            [],
        ).append(row)

    constraint_data = {}

    for row in constraints:

        constraint_data.setdefault(
            row["table_name"],
            [],
        ).append(row)

    documents = []

    for (
        table_name,
        table_columns,
    ) in tables.items():

        lines = [
            f"Database schema: {schema_name}",
            f"Table: {table_name}",
            "Columns:",
        ]

        for column in table_columns:

            lines.append(
                f'- {column["column_name"]}: '
                f'{column["data_type"]}, '
                f'nullable={column["is_nullable"]}'
            )

        table_constraints = (
            constraint_data.get(
                table_name,
                [],
            )
        )

        if table_constraints:

            lines.append(
                "Constraints:"
            )

            for constraint in (
                table_constraints
            ):

                line = (
                    f'- {constraint["constraint_type"]}: '
                    f'{constraint["column_name"]}'
                )

                if constraint[
                    "foreign_table_name"
                ]:

                    line += (
                        f' -> '
                        f'{constraint["foreign_table_name"]}'
                        f'.'
                        f'{constraint["foreign_column_name"]}'
                    )

                lines.append(line)

        documents.append(
            Document(
                page_content="\n".join(
                    lines
                ),
                metadata={
                    "datasource": datasource_name,
                    "schema": schema_name,
                    "table": table_name,
                    "type": "database_schema",
                },
            )
        )

    return documents