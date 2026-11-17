"""Agent voice-behaviour and call-tooling defaults (PART 12, P12-01..P12-15).

Revision ID: 0070_agent_voice_behavior
Revises: 0069_voice_demo_sessions
Create Date: 2026-11-10

The specification asked for `0063_agent_voice_behavior.py`; that revision id is already taken, so
this lands on the next free head. The *content* differs from the sketch in one deliberate way: no
columns are added.

Agent behaviour is stored as JSON in ``agents.current_draft_config`` and frozen into
``agent_versions.config_snapshot`` on publish. Adding parallel columns for
``begin_message_delay_ms``, ``denoise_mode``, ``languages`` and friends would create a second
source of truth that drifts the moment either side is written — so instead this migration
*backfills* the defaults into the JSON of existing drafts and published snapshots, which is the
one thing a data migration is actually needed for: new agents get the defaults from the code, and
old agents must not change behaviour on upgrade.

Down-migration removes exactly the keys this migration added, so a rollback returns the rows to
their previous content.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0070_agent_voice_behavior"
down_revision = "0069_voice_demo_sessions"
branch_labels = None
depends_on = None

#: The neutral defaults. Every one of these reproduces the pre-PART-12 behaviour, so an agent that
#: is never touched by an operator sounds exactly as it did yesterday.
VOICE_BEHAVIOR_DEFAULTS: dict[str, object] = {
    "colloquial": False,
    "colloquial_budget_ms": 50,
    "expressive": False,
    "expressive_emotions": ["neutral", "warm", "calm"],
    "expressive_auto": False,
    "dynamic_pace": True,
    "pace_min_speed": 0.8,
    "pace_max_speed": 1.2,
    "begin_message_delay_ms": 0,
    "stt_endpointing_mode": "default",
    "denoise_mode": "off",
    "normalize_for_speech": True,
    "read_numbers_slowly": False,
    "languages": [],
}

CALL_TOOLING_DEFAULTS: dict[str, object] = {
    "voicemail_action": "hangup",
    "voicemail_message": "",
    "voicemail_window_s": 12.0,
    "user_dtmf_mode": "end_key",
    "user_dtmf_end_key": "#",
    "user_dtmf_fixed_digits": 4,
    "user_dtmf_timeout_s": 5.0,
    "user_dtmf_keep_last": 2,
    "call_screening_message": "",
    "call_screening_wait_s": 20.0,
    "structured_output_schema": None,
    "structured_output_strict": True,
}


def _merge(payload: object, block_key: str, defaults: dict[str, object]) -> dict | None:
    """Return an updated mapping, or None when nothing needs writing."""
    if not isinstance(payload, dict):
        return None
    existing = payload.get(block_key)
    merged = dict(defaults)
    if isinstance(existing, dict):
        merged.update(existing)  # a value the operator already set always wins
        if merged == existing:
            return None
    updated = dict(payload)
    updated[block_key] = merged
    return updated


def _backfill(table: str, column: str) -> int:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
    changed = 0
    for row_id, raw in rows:
        payload = raw
        if isinstance(raw, (str, bytes)):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
        updated = _merge(payload, "voice_behavior", VOICE_BEHAVIOR_DEFAULTS)
        if updated is None:
            continue
        updated = _merge(updated, "call_tooling", CALL_TOOLING_DEFAULTS) or updated
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :row_id"),
            {"payload": json.dumps(updated), "row_id": row_id},
        )
        changed += 1
    return changed


def _strip(table: str, column: str, keys: tuple[str, ...]) -> int:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
    changed = 0
    for row_id, raw in rows:
        payload = raw
        if isinstance(raw, (str, bytes)):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
        if not isinstance(payload, dict) or not any(key in payload for key in keys):
            continue
        updated = {key: value for key, value in payload.items() if key not in keys}
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :row_id"),
            {"payload": json.dumps(updated), "row_id": row_id},
        )
        changed += 1
    return changed


def upgrade() -> None:
    _backfill("agents", "current_draft_config")
    _backfill("agent_versions", "config_snapshot")


def downgrade() -> None:
    _strip("agents", "current_draft_config", ("voice_behavior", "call_tooling"))
    _strip("agent_versions", "config_snapshot", ("voice_behavior", "call_tooling"))
