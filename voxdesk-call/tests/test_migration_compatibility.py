"""Tests for safe normalization of legacy overlength Alembic revision IDs."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.migration_compatibility import (
    LEGACY_REVISION_ALIASES,
    normalize_legacy_revision_aliases,
)


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    list(LEGACY_REVISION_ALIASES.items()),
)
def test_canonical_revision_ids_fit_default_alembic_version_column(legacy, canonical):
    assert len(legacy) > 32
    assert len(canonical) <= 32


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    list(LEGACY_REVISION_ALIASES.items()),
)
def test_known_legacy_stamp_is_rewritten_without_schema_changes(legacy, canonical):
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
                {"revision": legacy},
            )

        with engine.connect() as connection:
            rewritten = normalize_legacy_revision_aliases(connection)
            rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
        assert rewritten == 1
        assert rows == [canonical]
    finally:
        engine.dispose()


def test_every_legacy_alias_targets_a_revision_in_the_current_graph():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for canonical in LEGACY_REVISION_ALIASES.values():
        revision = script.get_revision(canonical)
        assert revision is not None
        assert revision.revision == canonical


def test_unrecognized_revision_and_missing_table_are_left_untouched():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            assert normalize_legacy_revision_aliases(connection) == 0

        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
                {"revision": "0037_enterprise_missing_apis"},
            )

        with engine.connect() as connection:
            rewritten = normalize_legacy_revision_aliases(connection)
            rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
        assert rewritten == 0
        assert rows == ["0037_enterprise_missing_apis"]
    finally:
        engine.dispose()


def test_conflicting_legacy_and_canonical_stamps_fail_closed():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) "
                    "VALUES (:legacy), (:canonical)"
                ),
                {
                    "legacy": "0038_agent_chat_contact_conductor",
                    "canonical": "0038_agent_chat_conductor",
                },
            )

        with engine.connect() as connection:
            with pytest.raises(RuntimeError, match="Ambiguous Alembic version state"):
                normalize_legacy_revision_aliases(connection)
            rows = connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars().all()
        assert rows == [
            "0038_agent_chat_conductor",
            "0038_agent_chat_contact_conductor",
        ]
    finally:
        engine.dispose()


def test_boolean_migration_defaults_avoid_integer_literals():
    """PostgreSQL rejects numeric server defaults on Boolean columns."""

    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    unsafe_defaults = []
    for migration_path in versions_dir.glob("*.py"):
        tree = ast.parse(migration_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "Column":
                continue
            if len(node.args) < 2:
                continue
            column_type = node.args[1]
            if not (
                isinstance(column_type, ast.Call)
                and isinstance(column_type.func, ast.Attribute)
                and column_type.func.attr in {"Boolean", "BOOLEAN"}
            ):
                continue
            column_name = (
                node.args[0].value
                if isinstance(node.args[0], ast.Constant)
                else "<dynamic>"
            )
            for keyword in node.keywords:
                if keyword.arg != "server_default":
                    continue
                default = keyword.value
                literal = None
                if isinstance(default, ast.Constant):
                    literal = default.value
                elif (
                    isinstance(default, ast.Call)
                    and isinstance(default.func, ast.Attribute)
                    and default.func.attr == "text"
                    and default.args
                    and isinstance(default.args[0], ast.Constant)
                ):
                    literal = default.args[0].value
                if literal in {0, 1, "0", "1"}:
                    unsafe_defaults.append(
                        f"{migration_path.name}:{node.lineno}:{column_name}={literal!r}"
                    )
    assert unsafe_defaults == [], (
        "Boolean server defaults must use dialect-aware true/false expressions: "
        + ", ".join(unsafe_defaults)
    )


def test_membership_migration_reuses_the_existing_postgresql_userrole_enum(monkeypatch):
    """Revision 0018 must not issue CREATE TYPE for the baseline enum."""
    from sqlalchemy.dialects import postgresql

    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_path = versions_dir / "0018_org_memberships_quotas.py"
    spec = importlib.util.spec_from_file_location("_migration_0018_enum_check", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: SimpleNamespace(
                dialect=SimpleNamespace(name="postgresql")
            )
        ),
    )
    role_type = migration._role_type()
    assert isinstance(role_type, postgresql.ENUM)
    assert role_type.name == "userrole"
    assert role_type.create_type is False
