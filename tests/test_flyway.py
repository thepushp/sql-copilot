import pytest

from conftest import make_metadata, make_table
from database.database_inspector import apply_flyway_state
from flyway.flyway_inspector import (
    build_flyway_object_state,
    inspect_flyway_directory,
    parse_migration_filename,
)
from flyway.migration_metadata import MigrationOperation


@pytest.mark.parametrize(
    "name,expected",
    [
        ("V001__create_student.sql", ("001", "create student")),
        ("V1_2__add_col.sql", ("1_2", "add col")),
        ("v145__drop_student_equipment.sql", ("145", "drop student equipment")),
        ("R__refresh_views.sql", ("R", "refresh views")),
        ("notes.sql", ("UNKNOWN", "notes.sql")),
        ("/some/dir/V7__x.sql", ("7", "x")),
    ],
)
def test_parse_migration_filename(name, expected):
    assert parse_migration_filename(name) == expected


@pytest.fixture
def migrations(tmp_path):
    files = {
        "V001__create_student.sql": "CREATE TABLE student (id bigint);",
        "V002__create_equipment.sql":
            "CREATE TABLE IF NOT EXISTS public.equipment (id bigint);",
        "V003__alter_student.sql": "ALTER TABLE student ADD COLUMN x int;",
        "V004__drop_equipment.sql": "DROP TABLE IF EXISTS public.equipment;",
        "V005__recreate_equipment.sql": "CREATE TABLE public.equipment (id bigint);",
        "V006__other_schema.sql": 'CREATE TABLE audit."log" (id bigint);',
        "V007__data_only.sql": "INSERT INTO student VALUES (1);",
        "readme.txt": "not a migration",
    }
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return tmp_path


def test_directory_scan_reads_only_sql_files(migrations):
    found = inspect_flyway_directory(str(migrations))

    assert len(found) == 7
    assert found[0].filename == "V001__create_student.sql"


def test_operations_are_detected(migrations):
    ops = {
        m.filename: m.operation
        for m in inspect_flyway_directory(str(migrations))
    }

    assert ops["V001__create_student.sql"] == MigrationOperation.CREATE
    assert ops["V003__alter_student.sql"] == MigrationOperation.ALTER
    assert ops["V004__drop_equipment.sql"] == MigrationOperation.DROP
    assert ops["V007__data_only.sql"] == MigrationOperation.UNKNOWN


def test_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        inspect_flyway_directory(str(tmp_path / "nope"))


def test_object_state_follows_create_drop_recreate(migrations):
    state = build_flyway_object_state(
        inspect_flyway_directory(str(migrations))
    )

    assert state["public.student"].exists
    assert state["public.student"].last_migration == "V003__alter_student.sql"
    assert state["public.equipment"].exists
    assert state["public.equipment"].dropped_by is None
    assert state["public.equipment"].created_by == "V005__recreate_equipment.sql"
    assert "audit.log" in state


def test_dropped_table_state(tmp_path):
    (tmp_path / "V1__a.sql").write_text("CREATE TABLE t (id int);")
    (tmp_path / "V2__b.sql").write_text("DROP TABLE t;")

    state = build_flyway_object_state(inspect_flyway_directory(str(tmp_path)))

    assert not state["public.t"].exists
    assert state["public.t"].dropped_by == "V2__b.sql"


def test_apply_state_marks_tables(tmp_path):
    (tmp_path / "V1__a.sql").write_text("CREATE TABLE alive (id int);")
    (tmp_path / "V2__b.sql").write_text("CREATE TABLE gone (id int);")
    (tmp_path / "V3__c.sql").write_text("DROP TABLE gone;")
    alive, gone, unmanaged = (
        make_table("alive"), make_table("gone"), make_table("manual")
    )
    metadata = make_metadata(("public", [alive, gone, unmanaged]))

    apply_flyway_state(metadata, str(tmp_path))

    assert alive.flyway_managed and alive.flyway_active
    assert gone.flyway_managed and not gone.flyway_active
    assert gone.flyway_dropped_by == "V3__c.sql"
    assert not unmanaged.flyway_managed and unmanaged.flyway_active
