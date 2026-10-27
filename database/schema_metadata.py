from dataclasses import dataclass, field

from langchain_core.documents import Document


VIEW_KINDS = ("view", "materialized view")


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    nullable: str
    default: str | None
    primary_key: bool = False
    comment: str | None = None
    values: list[str] = field(default_factory=list)
    values_kind: str = ""


@dataclass
class ForeignKeyMetadata:
    column: str
    foreign_schema: str
    foreign_table: str
    foreign_column: str
    inferred: bool = False


@dataclass
class IndexMetadata:
    name: str
    columns: list[str]
    unique: bool
    method: str
    definition: str | None = None


@dataclass
class ConstraintMetadata:
    name: str
    kind: str  # "unique" or "check"
    definition: str
    columns: list[str] = field(default_factory=list)


@dataclass
class TriggerMetadata:
    name: str
    definition: str


@dataclass
class TableMetadata:
    schema: str
    name: str
    columns: list[ColumnMetadata] = field(
        default_factory=list
    )
    foreign_keys: list[ForeignKeyMetadata] = field(
        default_factory=list
    )
    indexes: list[IndexMetadata] = field(
        default_factory=list
    )

    # Flyway information
    flyway_managed: bool = False
    flyway_active: bool = True
    flyway_created_by: str | None = None
    flyway_dropped_by: str | None = None
    flyway_last_migration: str | None = None

    kind: str = "table"
    comment: str | None = None
    approx_rows: int | None = None

    # Shown in the Explorer only; never sent to the model.
    constraints: list[ConstraintMetadata] = field(default_factory=list)
    definition: str | None = None
    size_bytes: int | None = None
    triggers: list[TriggerMetadata] = field(default_factory=list)


@dataclass
class SchemaMetadata:
    name: str
    tables: list[TableMetadata] = field(
        default_factory=list
    )


@dataclass
class DatabaseMetadata:
    schemas: list[SchemaMetadata] = field(
        default_factory=list
    )

    @property
    def schema_count(self):
        return len(self.schemas)

    @property
    def table_count(self):
        return sum(
            1
            for schema in self.schemas
            for table in schema.tables
            if table.kind not in VIEW_KINDS
        )

    @property
    def view_count(self):
        return sum(
            1
            for schema in self.schemas
            for table in schema.tables
            if table.kind in VIEW_KINDS
        )

    def to_documents(self):

        documents = []

        related = self._related_tables()

        for schema in self.schemas:

            for table in schema.tables:

                if not table.flyway_active:
                    continue

                key = f"{schema.name}.{table.name}"

                lines = [
                    f"Schema: {schema.name}",
                    f"Table: {table.name}",
                ]

                if table.kind != "table":

                    lines.append(f"Type: {table.kind}")

                if table.comment:

                    lines.append(
                        f"Description: {table.comment}"
                    )

                if table.approx_rows and table.approx_rows > 0:

                    lines.append(
                        f"Approx rows: {table.approx_rows:,}"
                    )

                # ---------------------------------------------
                # Flyway state
                # ---------------------------------------------

                if table.flyway_managed:

                    if table.flyway_active:

                        lines.append(
                            "Flyway status: ACTIVE"
                        )

                    else:

                        lines.append(
                            "Flyway status: DROPPED / INACTIVE"
                        )

                    if table.flyway_created_by:

                        lines.append(
                            f"Created by Flyway: "
                            f"{table.flyway_created_by}"
                        )

                    if table.flyway_dropped_by:

                        lines.append(
                            f"Dropped by Flyway: "
                            f"{table.flyway_dropped_by}"
                        )

                    if table.flyway_last_migration:

                        lines.append(
                            f"Last Flyway migration: "
                            f"{table.flyway_last_migration}"
                        )

                # ---------------------------------------------
                # Columns
                # ---------------------------------------------

                lines.append("Columns:")

                for column in table.columns:

                    pk = (
                        ", primary_key=true"
                        if column.primary_key
                        else ""
                    )

                    extras = ""

                    if column.values:

                        label = (
                            "allowed_values"
                            if column.values_kind == "enum"
                            else "common_values"
                        )

                        extras += (
                            f", {label}=["
                            + ", ".join(column.values)
                            + "]"
                        )

                    if column.comment:

                        extras += (
                            f", description={column.comment}"
                        )

                    lines.append(
                        f"- {column.name}: "
                        f"{column.data_type}, "
                        f"nullable={column.nullable}, "
                        f"default={column.default}"
                        f"{pk}"
                        f"{extras}"
                    )

                # ---------------------------------------------
                # Relationships
                # ---------------------------------------------

                if table.foreign_keys:

                    lines.append(
                        "Relationships:"
                    )

                    for fk in table.foreign_keys:

                        relationship_type = (
                            "inferred"
                            if fk.inferred
                            else "explicit_foreign_key"
                        )

                        lines.append(
                            f"- {table.schema}."
                            f"{table.name}."
                            f"{fk.column} -> "
                            f"{fk.foreign_schema}."
                            f"{fk.foreign_table}."
                            f"{fk.foreign_column} "
                            f"(type={relationship_type})"
                        )

                # ---------------------------------------------
                # Indexes
                # ---------------------------------------------

                if table.indexes:

                    lines.append("Indexes:")

                    for index in table.indexes:

                        index_columns = [
                            str(column)
                            for column in index.columns
                            if column is not None
                        ]

                        column_text = (
                            ", ".join(index_columns)
                            if index_columns
                            else "expression/unknown"
                        )

                        lines.append(
                            f"- {index.name}: "
                            f"({column_text}), "
                            f"unique={index.unique}, "
                            f"method={index.method}"
                        )

                documents.append(
                    Document(
                        page_content="\n".join(lines),
                        metadata={
                            "schema": schema.name,
                            "table": table.name,
                            "type": "database_schema",
                            "key": key,
                            "kind": table.kind,
                            "columns": [
                                column.name
                                for column in table.columns
                            ],
                            "values": [
                                value
                                for column in table.columns
                                for value in column.values
                            ],
                            "search_text": _search_text(table),
                            "summary": _summary_line(table),
                            "related": sorted(
                                related.get(key, ())
                            ),
                            "flyway_active": (
                                table.flyway_active
                            ),
                        },
                    )
                )

        return documents

    def _related_tables(self):

        active = {
            f"{schema.name}.{table.name}"
            for schema in self.schemas
            for table in schema.tables
            if table.flyway_active
        }

        related = {key: set() for key in active}

        for schema in self.schemas:

            for table in schema.tables:

                source = f"{schema.name}.{table.name}"

                if source not in active:
                    continue

                for fk in table.foreign_keys:

                    target = (
                        f"{fk.foreign_schema}."
                        f"{fk.foreign_table}"
                    )

                    if target in active and target != source:

                        related[source].add(target)
                        related[target].add(source)

        return related


def _words(name):

    return name.replace("_", " ")


def _search_text(table):

    parts = [
        f"{_words(table.name)} "
        f"{'' if table.kind == 'table' else table.kind + ' '}"
        f"in {_words(table.schema)}"
    ]

    if table.comment:
        parts.append(table.comment)

    parts.append(
        "columns: "
        + ", ".join(_words(column.name) for column in table.columns)
    )

    value_parts = [
        f"{_words(column.name)} {', '.join(column.values[:6])}"
        for column in table.columns
        if column.values
    ]

    if value_parts:
        parts.append("values: " + "; ".join(value_parts))

    return ". ".join(parts)


def _summary_line(table):

    columns = [column.name for column in table.columns]

    shown = ", ".join(columns[:12]) + (
        ", ..." if len(columns) > 12 else ""
    )

    line = f"{table.schema}.{table.name}"

    if table.kind != "table":
        line += f" [{table.kind}]"

    line += f": {shown}"

    value_hints = [
        f"{column.name}={'|'.join(column.values[:5])}"
        for column in table.columns
        if column.values
    ][:3]

    if value_hints:
        line += " [" + "; ".join(value_hints) + "]"

    if table.comment:
        line += f" -- {table.comment}"

    if table.approx_rows and table.approx_rows > 0:
        line += f" (~{table.approx_rows:,} rows)"

    return line


def infer_relationships(
    database_metadata: DatabaseMetadata,
) -> None:

    table_map = {
        (
            table.schema.lower(),
            table.name.lower(),
        ): table
        for schema in database_metadata.schemas
        for table in schema.tables
        if table.flyway_active
    }

    for child_table in table_map.values():

        for column in child_table.columns:

            column_name = column.name.lower()

            if not column_name.endswith("_id"):
                continue

            parent_table_name = column_name[:-3]

            if not parent_table_name:
                continue

            parent_table = table_map.get(
                (
                    child_table.schema.lower(),
                    parent_table_name,
                )
            )

            if parent_table is None:
                continue

            parent_id = next(
                (
                    parent_column
                    for parent_column in parent_table.columns
                    if parent_column.name.lower() == "id"
                ),
                None,
            )

            if parent_id is None:
                continue

            already_exists = any(
                fk.column.lower()
                == column.name.lower()
                and fk.foreign_schema.lower()
                == parent_table.schema.lower()
                and fk.foreign_table.lower()
                == parent_table.name.lower()
                and fk.foreign_column.lower()
                == parent_id.name.lower()
                for fk in child_table.foreign_keys
            )

            if already_exists:
                continue

            child_table.foreign_keys.append(
                ForeignKeyMetadata(
                    column=column.name,
                    foreign_schema=parent_table.schema,
                    foreign_table=parent_table.name,
                    foreign_column=parent_id.name,
                    inferred=True,
                )
            )

GENERIC_KEY_NAMES = frozenset(
    {
        "id", "name", "type", "code", "status", "key", "value", "label",
        "title", "description", "created_at", "updated_at", "version",
    }
)


def infer_shared_key_relationships(
    database_metadata: DatabaseMetadata,
) -> None:
    """Link tables that carry the same identifier column.

    If one table has a single-column primary key or unique index (such as
    `sub`), other tables in the same schema with a column of that name, or
    `<prefix>_<name>`, and the same type, point to it. Marked inferred.
    """

    for schema in database_metadata.schemas:

        active = [table for table in schema.tables if table.flyway_active]

        for parent in active:

            unique = {
                index.columns[0].lower()
                for index in parent.indexes
                if index.unique and len(index.columns) == 1 and index.columns[0]
            }

            unique |= {
                column.name.lower()
                for column in parent.columns
                if column.primary_key
            }

            for key in unique:

                if key in GENERIC_KEY_NAMES or len(key) < 3:
                    continue

                parent_column = next(
                    (
                        column
                        for column in parent.columns
                        if column.name.lower() == key
                    ),
                    None,
                )

                if parent_column is None:
                    continue

                for child in active:

                    if child is parent or child.kind != "table":
                        continue

                    for column in child.columns:

                        name = column.name.lower()

                        if name != key and not name.endswith("_" + key):
                            continue

                        if column.data_type != parent_column.data_type:
                            continue

                        if any(
                            fk.column.lower() == name
                            and fk.foreign_table.lower() == parent.name.lower()
                            for fk in child.foreign_keys
                        ):
                            continue

                        child.foreign_keys.append(
                            ForeignKeyMetadata(
                                column=column.name,
                                foreign_schema=parent.schema,
                                foreign_table=parent.name,
                                foreign_column=parent_column.name,
                                inferred=True,
                            )
                        )
