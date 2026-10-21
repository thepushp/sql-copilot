from sqlalchemy import inspect
from sqlalchemy.engine.reflection import ObjectKind

from database.connection_manager import get_engine
from database.dialects import get_dialect
from database.schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    SchemaMetadata,
    TableMetadata,
)


def _safe(function, *args, **kwargs):
    """Metadata a dialect cannot provide is simply left out."""

    try:
        return function(*args, **kwargs)
    except (NotImplementedError, Exception):
        return {}


def _type_text(column, engine):

    try:
        return column["type"].compile(dialect=engine.dialect)
    except Exception:
        return str(column["type"])


def _schemas(inspector, config, dialect):

    configured = [
        name.strip()
        for name in str(config.get("schemas") or "").split(",")
        if name.strip()
    ]

    if configured:
        return configured

    if dialect.key == "sqlite":
        return [None]

    if dialect.key == "mysql":
        return [config["database"]]

    if dialect.key == "oracle":
        return [config["username"].upper()]

    system = {name.lower() for name in dialect.system_schemas}

    return [
        name
        for name in inspector.get_schema_names()
        if name.lower() not in system
    ]


def inspect_generic(
    datasource_name: str,
    config: dict,
) -> DatabaseMetadata:
    """Dialect-neutral metadata via SQLAlchemy reflection."""

    dialect = get_dialect(config)

    engine = get_engine(datasource_name, config)

    inspector = inspect(engine)

    result = []

    for schema in _schemas(inspector, config, dialect):

        views = set(_safe(inspector.get_view_names, schema=schema) or [])

        columns = inspector.get_multi_columns(
            schema=schema,
            kind=ObjectKind.ANY,
        )

        primary_keys = _safe(
            inspector.get_multi_pk_constraint,
            schema=schema,
            kind=ObjectKind.ANY,
        )

        foreign_keys = _safe(
            inspector.get_multi_foreign_keys,
            schema=schema,
            kind=ObjectKind.ANY,
        )

        indexes = _safe(
            inspector.get_multi_indexes,
            schema=schema,
            kind=ObjectKind.ANY,
        )

        comments = _safe(
            inspector.get_multi_table_comment,
            schema=schema,
            kind=ObjectKind.ANY,
        )

        display_schema = schema or "main"

        schema_metadata = SchemaMetadata(name=display_schema)

        for (_, table_name), column_rows in sorted(
            columns.items(),
            key=lambda item: item[0][1],
        ):

            key = (schema, table_name)

            pk_columns = set(
                (primary_keys.get(key) or {}).get("constrained_columns")
                or []
            )

            table = TableMetadata(
                schema=display_schema,
                name=table_name,
                kind="view" if table_name in views else "table",
                comment=(comments.get(key) or {}).get("text"),
            )

            for column in column_rows:

                table.columns.append(
                    ColumnMetadata(
                        name=column["name"],
                        data_type=_type_text(column, engine),
                        nullable="YES" if column.get("nullable", True) else "NO",
                        default=(
                            None
                            if column.get("default") is None
                            else str(column["default"])
                        ),
                        primary_key=column["name"] in pk_columns,
                        comment=column.get("comment"),
                    )
                )

            for foreign_key in foreign_keys.get(key) or []:

                targets = foreign_key.get("referred_columns") or []

                for position, column_name in enumerate(
                    foreign_key.get("constrained_columns") or []
                ):

                    if position >= len(targets):
                        continue

                    table.foreign_keys.append(
                        ForeignKeyMetadata(
                            column=column_name,
                            foreign_schema=(
                                foreign_key.get("referred_schema")
                                or display_schema
                            ),
                            foreign_table=foreign_key["referred_table"],
                            foreign_column=targets[position],
                        )
                    )

            for index in indexes.get(key) or []:

                table.indexes.append(
                    IndexMetadata(
                        name=index.get("name") or "unnamed",
                        columns=[
                            str(name)
                            for name in index.get("column_names") or []
                            if name is not None
                        ],
                        unique=bool(index.get("unique")),
                        method="",
                    )
                )

            schema_metadata.tables.append(table)

        if schema_metadata.tables:
            result.append(schema_metadata)

    return DatabaseMetadata(
        schemas=sorted(result, key=lambda schema: schema.name)
    )
