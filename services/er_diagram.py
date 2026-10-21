import html
import re
from collections import deque


MAX_TABLES = 40
MAX_COLUMNS = 25

COLUMN_MODES = ("Keys only", "All columns", "Names only")
DEFAULT_COLUMN_MODE = "All columns"


def table_index(metadata):

    return {
        f"{table.schema}.{table.name}": table
        for schema in metadata.schemas
        for table in schema.tables
    }


def _edges(index, include_inferred):
    """Yield (child_key, fk, parent_key) for relationships between known tables."""

    for child_key, table in index.items():

        for fk in table.foreign_keys:

            if fk.inferred and not include_inferred:
                continue

            parent_key = f"{fk.foreign_schema}.{fk.foreign_table}"

            if parent_key in index and parent_key != child_key:

                yield child_key, fk, parent_key


def expand_around(index, start, hops, include_inferred=True):
    """Tables within `hops` relationship steps of `start`, closest first."""

    adjacency = {key: set() for key in index}

    for child, _, parent in _edges(index, include_inferred):

        adjacency[child].add(parent)
        adjacency[parent].add(child)

    seen = [start]
    queue = deque([(start, 0)])

    while queue:

        key, depth = queue.popleft()

        if depth == hops:
            continue

        for neighbor in sorted(adjacency.get(key, ())):

            if neighbor not in seen:

                seen.append(neighbor)
                queue.append((neighbor, depth + 1))

    return seen


def most_connected(index, include_inferred=True):

    degree = {key: 0 for key in index}

    for child, _, parent in _edges(index, include_inferred):

        degree[child] += 1
        degree[parent] += 1

    return max(sorted(degree), key=lambda key: degree[key], default=None)


def schema_tables(index, schema, include_inferred=True, hide_isolated=True):
    """Tables of one schema, best connected first."""

    keys = [key for key, table in index.items() if table.schema == schema]

    degree = {key: 0 for key in keys}

    for child, _, parent in _edges(index, include_inferred):

        if child in degree:
            degree[child] += 1

        if parent in degree:
            degree[parent] += 1

    if hide_isolated:
        keys = [key for key in keys if degree[key] > 0]

    return sorted(keys, key=lambda key: (-degree[key], key))


def _visible_columns(table, fk_columns, mode):

    if mode == "Names only":
        return []

    key_names = {column.name for column in table.columns if column.primary_key}
    key_names |= fk_columns

    if mode == "Keys only":
        return [c for c in table.columns if c.name in key_names]

    columns = list(table.columns)

    if len(columns) <= MAX_COLUMNS:
        return columns

    keep = [c for c in columns if c.name in key_names]

    keep_names = {c.name for c in keep}

    for column in columns:

        if len(keep) >= MAX_COLUMNS:
            break

        if column.name not in keep_names:

            keep.append(column)
            keep_names.add(column.name)

    order = {c.name: i for i, c in enumerate(columns)}

    return sorted(keep, key=lambda c: order[c.name])


def _fk_columns(index, key, keys, include_inferred):

    return {
        fk.column
        for child, fk, parent in _edges(index, include_inferred)
        if child == key and parent in keys
    }


def build_dot(index, keys, mode="Keys only", include_inferred=True):

    keys = list(keys)
    key_set = set(keys)

    multiple_schemas = len({index[key].schema for key in keys}) > 1

    lines = [
        "digraph ER {",
        '  rankdir=LR; nodesep=0.5; ranksep=1.0;',
        '  node [shape=plain fontname="Helvetica" fontsize=11];',
        '  edge [fontname="Helvetica" fontsize=9 color="#5a6b85" '
        'dir=both arrowtail=crow arrowhead=tee];',
    ]

    ports = {}

    for key in keys:

        table = index[key]

        fk_cols = _fk_columns(index, key, key_set, include_inferred)

        # parents are referenced on their own side, so show the columns they expose
        for child, fk, parent in _edges(index, include_inferred):

            if parent == key and child in key_set:
                fk_cols.add(fk.foreign_column)

        columns = _visible_columns(table, fk_cols, mode)

        header = html.escape(table.name)

        if multiple_schemas:
            header = f"{html.escape(table.schema)}.{header}"

        if table.kind != "table":
            header += f" <I>({html.escape(table.kind)})</I>"

        rows = [
            f'<TR><TD BGCOLOR="#dbe6f5" ALIGN="LEFT"><B>{header}</B></TD></TR>'
        ]

        for number, column in enumerate(columns):

            port = f"c{number}"

            ports[(key, column.name)] = port

            badge = "PK " if column.primary_key else (
                "FK " if column.name in fk_cols else ""
            )

            label = (
                f"{html.escape(badge)}{html.escape(column.name)}"
                f' : <FONT COLOR="#6b7280">{html.escape(column.data_type)}</FONT>'
            )

            if column.primary_key:
                label = f"<B>{label}</B>"

            rows.append(
                f'<TR><TD ALIGN="LEFT" PORT="{port}">{label}</TD></TR>'
            )

        lines.append(
            f'  "{key}" [label=<<TABLE BORDER="0" CELLBORDER="1" '
            f'CELLSPACING="0" CELLPADDING="4">{"".join(rows)}</TABLE>>];'
        )

    for child, fk, parent in _edges(index, include_inferred):

        if child not in key_set or parent not in key_set:
            continue

        tail = ports.get((child, fk.column))
        head = ports.get((parent, fk.foreign_column))

        tail_ref = f'"{child}":{tail}:w' if tail else f'"{child}"'
        head_ref = f'"{parent}":{head}:e' if head else f'"{parent}"'

        style = ' style=dashed color="#9ca3af"' if fk.inferred else ""

        lines.append(f"  {tail_ref} -> {head_ref} [{style.strip()}];")

    lines.append("}")

    return "\n".join(lines)


def _mermaid_name(key, index, used):

    table = index[key]

    name = re.sub(r"\W", "_", table.name)

    if name in used and used[name] != key:
        name = re.sub(r"\W", "_", f"{table.schema}_{table.name}")

    used[name] = key

    return name


def _mermaid_type(data_type):

    return re.sub(r"\W+", "_", data_type).strip("_") or "unknown"


def build_mermaid(index, keys, mode="Keys only", include_inferred=True):

    keys = list(keys)
    key_set = set(keys)

    used = {}
    names = {key: _mermaid_name(key, index, used) for key in keys}

    lines = ["erDiagram"]

    for key in keys:

        table = index[key]

        fk_cols = _fk_columns(index, key, key_set, include_inferred)

        for child, fk, parent in _edges(index, include_inferred):

            if parent == key and child in key_set:
                fk_cols.add(fk.foreign_column)

        columns = _visible_columns(table, fk_cols, mode)

        lines.append(f"  {names[key]} {{")

        for column in columns:

            marker = " PK" if column.primary_key else (
                " FK" if column.name in fk_cols else ""
            )

            column_name = re.sub(r"\W", "_", column.name)

            lines.append(
                f"    {_mermaid_type(column.data_type)} "
                f"{column_name}{marker}"
            )

        lines.append("  }")

    for child, fk, parent in _edges(index, include_inferred):

        if child in key_set and parent in key_set:

            label = fk.column + (" (inferred)" if fk.inferred else "")

            lines.append(
                f'  {names[parent]} ||--o{{ {names[child]} : "{label}"'
            )

    return "\n".join(lines)
