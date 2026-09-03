import time

import pandas as pd

from sqlalchemy import text

from database.connection_manager import (
    get_connection,
)

from models.query_result import QueryResult


MAX_ROWS = 500


def execute_query(
    datasource_name: str,
    sql: str,
) -> QueryResult:

    started = time.perf_counter()

    try:

        with get_connection(
            datasource_name
        ) as connection:

            result = connection.execute(
                text(sql)
            )

            rows = result.fetchmany(
                MAX_ROWS
            )

            dataframe = pd.DataFrame(
                rows,
                columns=list(
                    result.keys()
                ),
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