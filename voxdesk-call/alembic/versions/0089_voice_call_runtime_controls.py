"""Runtime-only voice and call controls for PART 12.

Revision ID: 0089_voice_call_runtime_controls
Revises: 0088_demo_call_requests

Revision 0070 added the original behaviour blocks. This additive follow-up backfills the newer
runtime controls into existing draft snapshots and published versions; missing values still
remain backwards-compatible in the domain parser.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0089_voice_call_runtime_controls"
down_revision = "0088_demo_call_requests"
branch_labels = None
depends_on = None

VOICE_DEFAULTS: dict[str, object] = {
    "format_transcript": True,
    "language_reply_policy": "follow_caller",
    "language_fallback_phrase": "",
}
CALL_DEFAULTS: dict[str, object] = {
    "voicemail_detection_enabled": False,
    "user_dtmf_enabled": False,
    "user_dtmf_prompt": "",
    "call_screening_enabled": False,
    "ivr_navigation_enabled": False,
    "ivr_goal": "",
    "auto_hangup_on_ivr": False,
    "ivr_max_menus": 3,
}


def _read(raw: object) -> dict | None:
    payload = raw
    if isinstance(raw, (str, bytes)):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _backfill(table: str, column: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
    for row_id, raw in rows:
        payload = _read(raw)
        if payload is None:
            continue
        updated = dict(payload)
        changed = False
        for key, defaults in (("voice_behavior", VOICE_DEFAULTS), ("call_tooling", CALL_DEFAULTS)):
            current = updated.get(key)
            block = dict(current) if isinstance(current, dict) else {}
            before = dict(block)
            for field, default in defaults.items():
                block.setdefault(field, default)
            if block != before or not isinstance(current, dict):
                updated[key] = block
                changed = True
        if changed:
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :row_id"),
                {"payload": json.dumps(updated), "row_id": row_id},
            )


def _remove_keys(table: str, column: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
    for row_id, raw in rows:
        payload = _read(raw)
        if payload is None:
            continue
        updated = dict(payload)
        changed = False
        for key, defaults in (("voice_behavior", VOICE_DEFAULTS), ("call_tooling", CALL_DEFAULTS)):
            current = updated.get(key)
            if not isinstance(current, dict):
                continue
            block = dict(current)
            for field, default in defaults.items():
                # Preserve a non-default value that an operator may have set after upgrade.
                # Values equal to the migration's backfilled default are safe to remove.
                if field in block and block[field] == default:
                    block.pop(field, None)
            if block != current:
                if block:
                    updated[key] = block
                else:
                    # Upgrade may have created this block solely to add defaults. Remove the
                    # now-empty container on downgrade rather than leaving migration residue.
                    updated.pop(key, None)
                changed = True
        if changed:
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :row_id"),
                {"payload": json.dumps(updated), "row_id": row_id},
            )


def upgrade() -> None:
    _backfill("agents", "current_draft_config")
    _backfill("agent_versions", "config_snapshot")


def downgrade() -> None:
    _remove_keys("agents", "current_draft_config")
    _remove_keys("agent_versions", "config_snapshot")
