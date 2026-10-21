from config.privacy import PrivacySettings, apply_privacy


def keys(metadata):
    return {
        f"{t.schema}.{t.name}"
        for s in metadata.schemas
        for t in s.tables
    }


def table(metadata, key):
    return next(
        t
        for s in metadata.schemas
        for t in s.tables
        if f"{t.schema}.{t.name}" == key
    )


def test_original_metadata_is_not_modified(sample_metadata):
    before = keys(sample_metadata)

    apply_privacy(sample_metadata, PrivacySettings(excluded_schemas=["ops"]))

    assert keys(sample_metadata) == before
    assert any(
        c.name == "password"
        for c in table(sample_metadata, "public.student").columns
    )


def test_excluded_schema_is_removed_and_blocked(sample_metadata):
    filtered, policy = apply_privacy(
        sample_metadata, PrivacySettings(excluded_schemas=["ops"])
    )

    assert "ops.audit" not in keys(filtered)
    assert all(s.name != "ops" for s in filtered.schemas)
    assert "ops.audit" in policy.blocked_tables


def test_excluded_table_is_removed_and_blocked(sample_metadata):
    filtered, policy = apply_privacy(
        sample_metadata, PrivacySettings(excluded_tables=["public.school"])
    )

    assert "public.school" not in keys(filtered)
    assert "public.school" in policy.blocked_tables


def test_exclusion_is_case_insensitive(sample_metadata):
    filtered, _ = apply_privacy(
        sample_metadata, PrivacySettings(excluded_tables=["PUBLIC.School"])
    )

    assert "public.school" not in keys(filtered)


def test_column_patterns_hide_columns(sample_metadata):
    filtered, policy = apply_privacy(sample_metadata, PrivacySettings())
    student = table(filtered, "public.student")

    assert "password" not in [c.name for c in student.columns]
    assert "name" in [c.name for c in student.columns]
    assert "password" in policy.blocked_columns
    assert "secret_token" in policy.blocked_columns


def test_tables_with_hidden_columns_are_star_restricted(sample_metadata):
    _, policy = apply_privacy(sample_metadata, PrivacySettings())

    assert "public.student" in policy.star_restricted
    assert "public.school" not in policy.star_restricted


def test_custom_patterns_and_empty_patterns(sample_metadata):
    filtered, _ = apply_privacy(
        sample_metadata,
        PrivacySettings(column_patterns=["name", "  ", ""]),
    )
    student = table(filtered, "public.student")

    assert "name" not in [c.name for c in student.columns]
    assert "password" in [c.name for c in student.columns]


def test_no_patterns_hides_nothing(sample_metadata):
    filtered, policy = apply_privacy(
        sample_metadata, PrivacySettings(column_patterns=[])
    )

    assert policy.blocked_columns == set()
    assert policy.star_restricted == set()


def test_sample_values_can_be_turned_off(sample_metadata):
    filtered, _ = apply_privacy(
        sample_metadata, PrivacySettings(include_sample_values=False)
    )
    status = table(filtered, "public.trip").columns[2]

    assert status.values == []


def test_enum_values_survive_when_sample_values_are_off(sample_metadata):
    trip = table(sample_metadata, "public.trip")
    trip.columns[2].values_kind = "enum"

    filtered, _ = apply_privacy(
        sample_metadata, PrivacySettings(include_sample_values=False)
    )

    assert table(filtered, "public.trip").columns[2].values == [
        "COMPLETED",
        "CANCELLED",
    ]


def test_infrastructure_tables_hidden_by_default_but_not_blocked(sample_metadata):
    filtered, policy = apply_privacy(sample_metadata, PrivacySettings())

    assert "public.flyway_schema_history" not in keys(filtered)
    assert "public.flyway_schema_history" not in policy.blocked_tables


def test_infrastructure_tables_can_be_shown(sample_metadata):
    filtered, _ = apply_privacy(
        sample_metadata, PrivacySettings(hide_infrastructure=False)
    )

    assert "public.flyway_schema_history" in keys(filtered)


def test_relationships_to_removed_tables_are_pruned(sample_metadata):
    filtered, _ = apply_privacy(
        sample_metadata, PrivacySettings(excluded_tables=["public.student"])
    )

    for fk in table(filtered, "public.trip").foreign_keys:
        assert fk.foreign_table != "student"


def test_empty_schemas_are_dropped(sample_metadata):
    filtered, _ = apply_privacy(
        sample_metadata,
        PrivacySettings(excluded_tables=["ops.audit"]),
    )

    assert [s.name for s in filtered.schemas] == ["public"]
