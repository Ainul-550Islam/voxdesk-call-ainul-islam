"""Migration coverage for persisted SIP gateway PCAP completeness metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0091_call_debug_pcap_metadata.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0091_call_debug_pcap_metadata", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_metadata_migration_adds_defaults_and_downgrades() -> None:
    migration = _load_migration()
    assert migration.revision == "0091_call_debug_pcap_metadata"
    assert migration.down_revision == "0090_telephony_phone_routing"

    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE call_debug_artifacts ("
                "id TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL DEFAULT 0)"
            )
        )
        connection.execute(
            sa.text("INSERT INTO call_debug_artifacts (id, size_bytes) VALUES ('old', 24)")
        )
        connection.commit()

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        row = connection.execute(
            sa.text(
                "SELECT packet_count, truncated FROM call_debug_artifacts WHERE id='old'"
            )
        ).one()
        assert row.packet_count == 0
        assert bool(row.truncated) is False

        connection.execute(
            sa.text(
                "UPDATE call_debug_artifacts SET packet_count=3, truncated=1 WHERE id='old'"
            )
        )
        connection.commit()
        stored = connection.execute(
            sa.text(
                "SELECT packet_count, truncated FROM call_debug_artifacts WHERE id='old'"
            )
        ).one()
        assert stored.packet_count == 3
        assert bool(stored.truncated) is True

        with Operations.context(context):
            migration.downgrade()

        columns = {column["name"] for column in sa.inspect(connection).get_columns("call_debug_artifacts")}
        assert "packet_count" not in columns
        assert "truncated" not in columns

    engine.dispose()


def test_artifact_model_exposes_capture_metadata() -> None:
    from app.db.telephony_models import CallDebugArtifact

    assert "packet_count" in CallDebugArtifact.__table__.c
    assert "truncated" in CallDebugArtifact.__table__.c
