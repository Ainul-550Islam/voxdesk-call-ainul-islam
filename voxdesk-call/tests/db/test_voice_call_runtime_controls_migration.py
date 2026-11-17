"""Upgrade/downgrade behavior for PART 12's JSON snapshot backfill."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0089_voice_call_runtime_controls.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0089_voice_call_runtime_controls", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_and_snapshot_backfill_round_trip() -> None:
    migration = _load_migration()
    assert migration.revision == "0089_voice_call_runtime_controls"
    assert migration.down_revision == "0088_demo_call_requests"

    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            sa.text("CREATE TABLE agents (id TEXT PRIMARY KEY, current_draft_config JSON NOT NULL)")
        )
        connection.execute(
            sa.text("CREATE TABLE agent_versions (id TEXT PRIMARY KEY, config_snapshot JSON NOT NULL)")
        )
        connection.execute(
            sa.text("INSERT INTO agents VALUES (:id, :payload)"),
            {
                "id": "draft",
                "payload": json.dumps(
                    {
                        "voice_behavior": {"expressive": True, "language_reply_policy": "primary_only"},
                        "call_tooling": {"ivr_max_menus": 7, "call_screening_enabled": True},
                    }
                ),
            },
        )
        connection.execute(
            sa.text("INSERT INTO agent_versions VALUES (:id, :payload)"),
            {"id": "version", "payload": json.dumps({"name": "published"})},
        )
        connection.commit()

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            migration.upgrade()  # backfill is idempotent

            draft = json.loads(
                connection.execute(
                    sa.text("SELECT current_draft_config FROM agents WHERE id='draft'")
                ).scalar_one()
            )
            version = json.loads(
                connection.execute(
                    sa.text("SELECT config_snapshot FROM agent_versions WHERE id='version'")
                ).scalar_one()
            )
            assert draft["voice_behavior"]["expressive"] is True
            assert draft["voice_behavior"]["language_reply_policy"] == "primary_only"
            assert draft["voice_behavior"]["format_transcript"] is True
            assert draft["call_tooling"]["ivr_max_menus"] == 7
            assert draft["call_tooling"]["call_screening_enabled"] is True
            assert draft["call_tooling"]["user_dtmf_enabled"] is False
            assert version["voice_behavior"]["format_transcript"] is True
            assert version["call_tooling"]["ivr_max_menus"] == 3

            migration.downgrade()

        draft_after = json.loads(
            connection.execute(
                sa.text("SELECT current_draft_config FROM agents WHERE id='draft'")
            ).scalar_one()
        )
        version_after = json.loads(
            connection.execute(
                sa.text("SELECT config_snapshot FROM agent_versions WHERE id='version'")
            ).scalar_one()
        )
        assert draft_after == {
            "voice_behavior": {"expressive": True, "language_reply_policy": "primary_only"},
            "call_tooling": {"ivr_max_menus": 7, "call_screening_enabled": True},
        }
        assert version_after == {"name": "published"}

    engine.dispose()
