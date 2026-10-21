"""Relationship lookups for one table, from the discovered metadata."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reference:
    """One relationship seen from the table being viewed."""

    column: str
    other_key: str
    other_column: str
    inferred: bool


def outgoing(table, index):
    """Tables this table points to (its foreign keys), that are still known."""

    found = []

    for fk in table.foreign_keys:

        other = f"{fk.foreign_schema}.{fk.foreign_table}"

        if other in index:
            found.append(
                Reference(fk.column, other, fk.foreign_column, fk.inferred)
            )

    return found


def incoming(key, index):
    """Tables that point at this one, as (their column -> this table's column)."""

    found = []

    for other_key, other in index.items():

        for fk in other.foreign_keys:

            if f"{fk.foreign_schema}.{fk.foreign_table}" == key:
                found.append(
                    Reference(fk.foreign_column, other_key, fk.column, fk.inferred)
                )

    return sorted(found, key=lambda ref: (ref.inferred, ref.other_key))


def column_targets(table, index):
    """column name -> "schema.table.column" it references, for the column grid."""

    return {
        ref.column: f"{ref.other_key.split('.', 1)[1]}.{ref.other_column}"
        + (" (inferred)" if ref.inferred else "")
        for ref in outgoing(table, index)
    }
