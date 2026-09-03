from dataclasses import dataclass, field

from langchain_core.documents import Document


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    nullable: str
    default: str | None
    primary_key: bool = False


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
class TableMetadata:
    schema: str
    name: str
    columns: list[ColumnMetadata] = field(default_factory=list)
    foreign_keys: list[ForeignKeyMetadata] = field(default_factory=list)
    indexes: list[IndexMetadata] = field(default_factory=list)


@dataclass
class SchemaMetadata:
    name: str
    tables: list[TableMetadata] = field(default_factory=list)


@dataclass
class DatabaseMetadata:
    schemas: list[SchemaMetadata] = field(default_factory=list)
    view_count: int = 0

    @property
    def schema_count(self):
        return len(self.schemas)

    @property
    def table_count(self):
        return sum(
            len(schema.tables)
            for schema in self.schemas
        )

    def to_documents(self):
        documents = []

        for schema in self.schemas:

            for table in schema.tables:

                lines = [
                    f"Schema: {schema.name}",
                    f"Table: {table.name}",
                    "Columns:",
                ]

                # -------------------------------------------------
                # Columns
                # -------------------------------------------------

                for column in table.columns:

                    pk = (
                        ", primary_key=true"
                        if column.primary_key
                        else ""
                    )

                    lines.append(
                        f"- {column.name}: "
                        f"{column.data_type}, "
                        f"nullable={column.nullable}, "
                        f"default={column.default}"
                        f"{pk}"
                    )

                # -------------------------------------------------
                # Relationships
                # -------------------------------------------------

                if table.foreign_keys:

                    lines.append("Relationships:")

                    for fk in table.foreign_keys:

                        relationship_type = (
                            "inferred"
                            if fk.inferred
                            else "explicit_foreign_key"
                        )

                        lines.append(
                            f"- {table.schema}.{table.name}."
                            f"{fk.column} -> "
                            f"{fk.foreign_schema}."
                            f"{fk.foreign_table}."
                            f"{fk.foreign_column} "
                            f"(type={relationship_type})"
                        )

                # -------------------------------------------------
                # Indexes
                # -------------------------------------------------

                if table.indexes:

                    lines.append("Indexes:")

                    for index in table.indexes:

                        index_columns = [
                            str(column)
                            for column in index.columns
                            if column is not None
                        ]

                        if index_columns:
                            column_text = ", ".join(
                                index_columns
                            )
                        else:
                            column_text = "expression/unknown"

                        lines.append(
                            f"- {index.name}: "
                            f"({column_text}), "
                            f"unique={index.unique}, "
                            f"method={index.method}"
                        )

                        if index.definition:
                            lines.append(
                                f"  Definition: "
                                f"{index.definition}"
                            )

                documents.append(
                    Document(
                        page_content="\n".join(lines),
                        metadata={
                            "schema": schema.name,
                            "table": table.name,
                            "type": "database_schema",
                        },
                    )
                )

        return documents


def infer_relationships(
    database_metadata: DatabaseMetadata,
) -> None:

    # -------------------------------------------------------------
    # Build table lookup
    # -------------------------------------------------------------

    table_map = {
        (
            table.schema.lower(),
            table.name.lower(),
        ): table
        for schema in database_metadata.schemas
        for table in schema.tables
    }

    # -------------------------------------------------------------
    # Inspect every table
    # -------------------------------------------------------------

    for child_table in table_map.values():

        for column in child_table.columns:

            column_name = column.name.lower()

            # We only infer relationships for columns ending in _id.
            #
            # Example:
            #
            # student_id
            # school_id
            # driver_id
            #
            if not column_name.endswith("_id"):
                continue

            parent_table_name = column_name[:-3]

            if not parent_table_name:
                continue

            # -----------------------------------------------------
            # Find parent table in the SAME schema
            # -----------------------------------------------------

            parent_table = table_map.get(
                (
                    child_table.schema.lower(),
                    parent_table_name,
                )
            )

            if parent_table is None:
                continue

            # -----------------------------------------------------
            # Parent must contain "id"
            # -----------------------------------------------------

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

            # -----------------------------------------------------
            # Check if explicit FK already exists
            # -----------------------------------------------------

            already_exists = any(
                fk.column.lower() == column.name.lower()
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

            # -----------------------------------------------------
            # Add inferred relationship
            # -----------------------------------------------------

            child_table.foreign_keys.append(
                ForeignKeyMetadata(
                    column=column.name,
                    foreign_schema=parent_table.schema,
                    foreign_table=parent_table.name,
                    foreign_column=parent_id.name,
                    inferred=True,
                )
            )