"""Schema/model parity and legacy-binding backfill for weighted telephony routing."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _REPO_ROOT / "alembic" / "versions" / "0090_telephony_phone_routing.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0090_telephony_phone_routing", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_telephony_routing_column_backfills_legacy_agent_bindings() -> None:
    migration = _load_migration()
    assert migration.revision == "0090_telephony_phone_routing"
    assert migration.down_revision == "0089_voice_call_runtime_controls"

    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE telephony_phone_numbers ("
                "id TEXT PRIMARY KEY, inbound_agent_id TEXT, outbound_agent_id TEXT)"
            )
        )
        connection.execute(
            sa.text("INSERT INTO telephony_phone_numbers VALUES ('n1', 'agent-in', 'agent-out')")
        )
        connection.execute(
            sa.text("INSERT INTO telephony_phone_numbers VALUES ('n2', NULL, NULL)")
        )
        connection.commit()

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        stored = connection.execute(
            sa.text("SELECT routing FROM telephony_phone_numbers WHERE id='n1'")
        ).scalar_one()
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored == {
            "inbound": [{"agent_id": "agent-in", "weight": 1.0}],
            "outbound": [{"agent_id": "agent-out", "weight": 1.0}],
            "sms": [],
        }
        assert connection.execute(
            sa.text("SELECT routing FROM telephony_phone_numbers WHERE id='n2'")
        ).scalar_one() is None

        with Operations.context(context):
            migration.downgrade()
        names = {column["name"] for column in sa.inspect(connection).get_columns("telephony_phone_numbers")}
        assert "routing" not in names

    engine.dispose()


def test_legacy_phone_number_orm_maps_the_0072_routing_column() -> None:
    from app.telephony.number_provisioning import PhoneNumber

    assert "routing" in PhoneNumber.__table__.c
