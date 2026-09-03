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

    columns: list[ColumnMetadata] = field(
        default_factory=list
    )

    foreign_keys: list[ForeignKeyMetadata] = field(
        default_factory=list
    )

    indexes: list[IndexMetadata] = field(
        default_factory=list
    )


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
                    "",
                    "Columns:",
                ]

                for column in table.columns:

                    primary_key = (
                        ", primary_key=true"
                        if column.primary_key
                        else ""
                    )

                    default = (
                        column.default
                        if column.default is not None
                        else "none"
                    )

                    lines.append(
                        f"- {column.name}: "
                        f"{column.data_type}, "
                        f"nullable={column.nullable}, "
                        f"default={default}"
                        f"{primary_key}"
                    )

                if table.foreign_keys:

                    lines.append("")
                    lines.append("Foreign Keys:")

                    for fk in table.foreign_keys:

                        lines.append(
                            f"- {fk.column} -> "
                            f"{fk.foreign_schema}."
                            f"{fk.foreign_table}."
                            f"{fk.foreign_column}"
                        )

                if table.indexes:
                    lines.append("Indexes:")

                    for idx in table.indexes:
                        index_columns = [
                            str(column)
                            for column in idx.columns
                            if column is not None
                        ]

                        column_text = (
                            ", ".join(index_columns)
                            if index_columns
                            else "expression/unknown"
                        )

                        lines.append(
                            f"- {idx.name}: "
                            f"({column_text}), "
                            f"unique={idx.unique}, "
                            f"method={idx.method}"
                        )

                        if idx.definition:
                            lines.append(
                                f"  Definition: {idx.definition}"
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