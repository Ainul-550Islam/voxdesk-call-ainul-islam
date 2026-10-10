"""Backfill smart turn-taking, backchannel, idle reminder, and voice settings defaults on Agent & AgentVersion configs (Sub-Phase 2B/2G).

Revision ID: 0060_agent_turn_settings
Revises: 0059_number_agent_binding
Create Date: 2026-10-08
"""
from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "0060_agent_turn_settings"
down_revision = "0059_number_agent_binding"
branch_labels = None
depends_on = None

DEFAULT_TURN_AND_VOICE_SETTINGS = {
    "responsiveness": 0.5,
    "interruption_sensitivity": 0.5,
    "turn_mode": "smart",
    "backchannel_enabled": False,
    "backchannel_frequency": 0.5,
    "backchannel_words": ["mm-hmm", "yeah", "got it", "right", "okay"],
    "filler_words_enabled": False,
    "reminder_trigger_ms": 10000,
    "reminder_max_count": 2,
    "end_call_after_silence_ms": 30000,
    "max_call_duration_ms": 1800000,
    "ambient_sound": "off",
    "ambient_volume": 0.15,
    "denoise_mode": "noisereduce",
    "voice_speed": 1.0,
    "voice_temperature": 0.5,
    "voice_volume": 1.0,
}


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _backfill_json_column(bind, table_name: str, col_name: str) -> None:
    if not _table_exists(bind, table_name):
        return
    rows = bind.execute(
        sa.text(f"SELECT id, {col_name} FROM {table_name}")
    ).fetchall()
    for row_id, raw_cfg in rows:
        if isinstance(raw_cfg, str):
            try:
                cfg = json.loads(raw_cfg) if raw_cfg else {}
            except Exception:
                cfg = {}
        elif isinstance(raw_cfg, dict):
            cfg = dict(raw_cfg)
        else:
            cfg = {}
        changed = False
        for k, v in DEFAULT_TURN_AND_VOICE_SETTINGS.items():
            if k not in cfg:
                cfg[k] = v
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} SET {col_name} = :cfg WHERE id = :id"
                ),
                {"cfg": json.dumps(cfg), "id": row_id},
            )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_json_column(bind, "agents", "current_draft_config")
    _backfill_json_column(bind, "agent_versions", "config_snapshot")


def downgrade() -> None:
    # Non-destructive JSON default backfill; downgrade is a safe no-op.
    pass
