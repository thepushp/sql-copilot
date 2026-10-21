import logging
import time

import pandas as pd

from sqlalchemy import text

from config.datasource_config import DATASOURCES
from database.connection_manager import (
    get_connection,
)
from database.dialects import get_dialect

from models.query_result import QueryResult


logger = logging.getLogger("sql_copilot.executor")

MAX_ROWS = 500

STATEMENT_TIMEOUT_MS = 30_000


def execute_query(
    datasource_name: str,
    sql: str,
    config: dict | None = None,
) -> QueryResult:

    started = time.perf_counter()

    dialect = get_dialect(config or DATASOURCES.get(datasource_name))

    try:

        with get_connection(
            datasource_name,
            config,
        ) as connection:

            for statement, required in dialect.setup:

                try:

                    connection.execute(
                        text(
                            statement.format(
                                ms=STATEMENT_TIMEOUT_MS,
                                s=STATEMENT_TIMEOUT_MS // 1000,
                            )
                        )
                    )

                except Exception as exc:

                    if required:
                        raise

                    logger.debug("Optional setup statement skipped: %s", exc)

            result = connection.execution_options(
                stream_results=True
            ).execute(text(sql))

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