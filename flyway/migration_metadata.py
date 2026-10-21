from dataclasses import dataclass, field
from enum import Enum


class MigrationOperation(str, Enum):
    CREATE = "CREATE"
    ALTER = "ALTER"
    DROP = "DROP"
    UNKNOWN = "UNKNOWN"


@dataclass
class MigrationMetadata:
    version: str
    description: str
    filename: str
    operation: MigrationOperation
    objects: list[str] = field(default_factory=list)


@dataclass
class FlywayObjectState:
    schema: str
    table: str
    exists: bool = True
    created_by: str | None = None
    dropped_by: str | None = None
    last_migration: str | None = None