import time

import pandas as pd

from sqlalchemy import text

from database.connection_manager import (
    get_connection,
)

from models.query_result import QueryResult


MAX_ROWS = 500

STATEMENT_TIMEOUT_MS = 30_000


def execute_query(
    datasource_name: str,
    sql: str,
) -> QueryResult:

    started = time.perf_counter()

    try:

        with get_connection(
            datasource_name
        ) as connection:

            connection.execute(
                text("SET TRANSACTION READ ONLY")
            )

            connection.execute(
                text(
                    "SET LOCAL statement_timeout = "
                    f"{STATEMENT_TIMEOUT_MS}"
                )
            )

            result = connection.execute(
                text(sql)
            )

            rows = result.fetchmany(
                MAX_ROWS
            )

            column_names = make_unique_column_names(
                list(result.keys())
            )

            dataframe = pd.DataFrame(
                rows,
                columns=column_names,
            )

        elapsed_ms = (
            time.perf_counter()
            - started
        ) * 1000

        return QueryResult(
            rows=dataframe,
            row_count=len(dataframe),
            execution_ms=elapsed_ms,
        )

    except Exception as exc:

        elapsed_ms = (
            time.perf_counter()
            - started
        ) * 1000

        return QueryResult(
            rows=None,
            row_count=0,
            execution_ms=elapsed_ms,
            error=str(exc),
        )
def make_unique_column_names(
    columns: list[str],
) -> list[str]:

    counts = {}
    result = []

    for column in columns:

        if column not in counts:
            counts[column] = 0
            result.append(column)
            continue

        counts[column] += 1

        result.append(
            f"{column}_{counts[column]}"
        )

    return result