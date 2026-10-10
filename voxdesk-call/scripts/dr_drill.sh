#!/usr/bin/env bash
# VoxDesk — Automated Disaster Recovery (DR) Drill (`scripts/dr_drill.sh`)
#
# Executes an end-to-end backup -> verify -> restore drill using:
#   1. scripts/backup.sh
#   2. scripts/backup_verify.sh
#   3. scripts/restore.sh
#
# Records real measured RPO (Recovery Point Objective) and RTO (Recovery Time
# Objective) along with pre/post row-count integrity verification in JSON.
#
# Usage:
#   ./scripts/dr_drill.sh [--output evidence/dr/dr_drill_report.json]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

REPORT_FILE="${DR_REPORT_FILE:-${REPO_ROOT}/evidence/dr/dr_drill_report.json}"
BACKUP_DIR="${BACKUP_DIR:-${REPO_ROOT}/evidence/dr/backups}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      REPORT_FILE="$2"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    *)
      echo "[dr-drill] ERROR: Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$(dirname "${REPORT_FILE}")" "${BACKUP_DIR}"

PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
PG_DRILL_USER="${PG_DRILL_USER:-voxdesk_dr}"
PG_DRILL_PASS="${PG_DRILL_PASS:-voxdesk_dr_pass}"
SOURCE_DB="${SOURCE_DB:-voxdesk_dr_source}"
TARGET_DB="${TARGET_DB:-voxdesk_dr_target}"

echo "[dr-drill] Ensuring PostgreSQL cluster is running on ${PG_HOST}:${PG_PORT}..."
if ! pg_isready -h "${PG_HOST}" -p "${PG_PORT}" >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    sudo service postgresql start
  fi
fi

pg_isready -h "${PG_HOST}" -p "${PG_PORT}" >/dev/null

echo "[dr-drill] Provisioning isolated drill databases (${SOURCE_DB}, ${TARGET_DB})..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL >/dev/null
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${PG_DRILL_USER}') THEN
    CREATE ROLE ${PG_DRILL_USER} WITH LOGIN SUPERUSER PASSWORD '${PG_DRILL_PASS}' CREATEDB;
  ELSE
    ALTER ROLE ${PG_DRILL_USER} WITH SUPERUSER CREATEDB PASSWORD '${PG_DRILL_PASS}';
  END IF;
END
\$\$;
DROP DATABASE IF EXISTS ${SOURCE_DB};
DROP DATABASE IF EXISTS ${TARGET_DB};
CREATE DATABASE ${SOURCE_DB} OWNER ${PG_DRILL_USER};
CREATE DATABASE ${TARGET_DB} OWNER ${PG_DRILL_USER};
SQL

SOURCE_ASYNC_URL="postgresql+asyncpg://${PG_DRILL_USER}:${PG_DRILL_PASS}@${PG_HOST}:${PG_PORT}/${SOURCE_DB}"
SOURCE_PG_URL="postgresql://${PG_DRILL_USER}:${PG_DRILL_PASS}@${PG_HOST}:${PG_PORT}/${SOURCE_DB}"
TARGET_PG_URL="postgresql://${PG_DRILL_USER}:${PG_DRILL_PASS}@${PG_HOST}:${PG_PORT}/${TARGET_DB}"

echo "[dr-drill] Applying Alembic migrations (head) to source database..."
DATABASE_URL="${SOURCE_ASYNC_URL}" alembic upgrade head

ALEMBIC_REV="$(psql "${SOURCE_PG_URL}" -Atc "SELECT version_num FROM alembic_version LIMIT 1;")"
echo "[dr-drill] Source database at Alembic revision: ${ALEMBIC_REV}"

echo "[dr-drill] Seeding pre-disaster organization, tenant, environment, user, agent, call, turn, and latency data..."
T_LAST_WRITE_NS="$(date +%s%N)"
T_LAST_WRITE_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

psql "${SOURCE_PG_URL}" -v ON_ERROR_STOP=1 <<'SQL' >/dev/null
INSERT INTO organizations (id, name, slug, status, created_at, updated_at)
VALUES ('11111111-1111-4111-8111-111111111101', 'VoxDesk DR Drill Org', 'voxdesk-dr-org', 'active', NOW(), NOW());

INSERT INTO tenants (id, organization_id, name, twilio_number, plan, is_active, is_test_tenant, lifecycle_status, created_at)
VALUES ('11111111-1111-4111-8111-111111111102', '11111111-1111-4111-8111-111111111101', 'VoxDesk DR Drill Tenant', '+14155550199', 'enterprise', true, false, 'active', NOW());

INSERT INTO environments (id, tenant_id, name, slug, kind, status, is_default, created_at, updated_at)
VALUES ('11111111-1111-4111-8111-111111111103', '11111111-1111-4111-8111-111111111102', 'Production', 'production', 'production', 'active', true, NOW(), NOW());

INSERT INTO users (id, tenant_id, email, full_name, password_hash, role, is_active, created_at, updated_at)
VALUES ('11111111-1111-4111-8111-111111111104', '11111111-1111-4111-8111-111111111102', 'dr-ops@voxdesk.local', 'DR Operator', '$argon2id$v=19$m=65536,t=3,p=4$drdrillplaceholder', 'OWNER', true, NOW(), NOW());

INSERT INTO agents (
  id, tenant_id, environment_id, external_key, name, slug, agent_type, status,
  current_draft_config, validation_errors, created_at, updated_at
)
VALUES (
  '11111111-1111-4111-8111-111111111105',
  '11111111-1111-4111-8111-111111111102',
  '11111111-1111-4111-8111-111111111103',
  'dr-concierge',
  'DR Voice Concierge',
  'dr-voice-concierge',
  'voice',
  'published',
  '{"voice_provider": "elevenlabs", "language": "en-US"}'::json,
  '[]'::json,
  NOW(),
  NOW()
);

INSERT INTO calls (
  id, tenant_id, environment_id, agent_id, call_sid, from_number, to_number,
  status, direction, started_at, ended_at, duration_seconds
)
VALUES
  (
    '11111111-1111-4111-8111-111111111106',
    '11111111-1111-4111-8111-111111111102',
    '11111111-1111-4111-8111-111111111103',
    '11111111-1111-4111-8111-111111111105',
    'CA_DR_DRILL_0001',
    '+14155550101',
    '+14155550199',
    'COMPLETED',
    'INBOUND',
    NOW() - INTERVAL '120 seconds',
    NOW() - INTERVAL '60 seconds',
    60.0
  ),
  (
    '11111111-1111-4111-8111-111111111107',
    '11111111-1111-4111-8111-111111111102',
    '11111111-1111-4111-8111-111111111103',
    '11111111-1111-4111-8111-111111111105',
    'CA_DR_DRILL_0002',
    '+14155550102',
    '+14155550199',
    'COMPLETED',
    'INBOUND',
    NOW() - INTERVAL '55 seconds',
    NOW() - INTERVAL '10 seconds',
    45.0
  );

INSERT INTO turns (id, call_id, speaker, text, latency_ms, created_at)
VALUES
  ('11111111-1111-4111-8111-111111111201', '11111111-1111-4111-8111-111111111106', 'USER', 'Hello, is my reservation confirmed?', NULL, NOW()),
  ('11111111-1111-4111-8111-111111111202', '11111111-1111-4111-8111-111111111106', 'ASSISTANT', 'Yes, your reservation is confirmed for tonight at 7 PM.', 294.7, NOW()),
  ('11111111-1111-4111-8111-111111111203', '11111111-1111-4111-8111-111111111107', 'USER', 'Can I update my billing address?', NULL, NOW()),
  ('11111111-1111-4111-8111-111111111204', '11111111-1111-4111-8111-111111111107', 'ASSISTANT', 'Certainly, I can help update your billing address.', 291.5, NOW());

INSERT INTO call_latency_stats (
  id, call_id, tenant_id, turn_idx, turns, stt_ms, llm_ttfb_ms, tts_ttfb_ms,
  e2e_ms, e2e_p50_ms, e2e_p95_ms, e2e_p99_ms, e2e_max_ms, interrupted, interruptions, created_at
)
VALUES
  (
    '11111111-1111-4111-8111-111111111301',
    '11111111-1111-4111-8111-111111111106',
    '11111111-1111-4111-8111-111111111102',
    1, 2, 88.4, 132.1, 74.2, 294.7, 294.7, 294.7, 294.7, 294.7, false, 0, NOW()
  ),
  (
    '11111111-1111-4111-8111-111111111302',
    '11111111-1111-4111-8111-111111111107',
    '11111111-1111-4111-8111-111111111102',
    1, 2, 91.0, 128.5, 72.0, 291.5, 291.5, 291.5, 291.5, 291.5, false, 0, NOW()
  );
SQL

echo "[dr-drill] Step 1/3: Running scripts/backup.sh against source database..."
T_BACKUP_START_NS="$(date +%s%N)"
PGHOST="${PG_HOST}" PGPORT="${PG_PORT}" POSTGRES_USER="${PG_DRILL_USER}" POSTGRES_DB="${SOURCE_DB}" PGPASSWORD="${PG_DRILL_PASS}" \
  sh "${SCRIPT_DIR}/backup.sh" "${BACKUP_DIR}"
T_BACKUP_END_NS="$(date +%s%N)"

BACKUP_FILE="$(ls -1t "${BACKUP_DIR}"/voxdesk-*.dump | head -n 1)"
if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
  echo "[dr-drill] ERROR: Backup file not found in ${BACKUP_DIR}" >&2
  exit 1
fi

echo "[dr-drill] Step 2/3: Running scripts/backup_verify.sh on ${BACKUP_FILE}..."
T_VERIFY_START_NS="$(date +%s%N)"
sh "${SCRIPT_DIR}/backup_verify.sh" "${BACKUP_FILE}"
T_VERIFY_END_NS="$(date +%s%N)"

echo "[dr-drill] Step 3/3: Simulating primary database loss and restoring into ${TARGET_DB} via scripts/restore.sh..."
T_DISASTER_START_NS="$(date +%s%N)"
T_DISASTER_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

PGHOST="${PG_HOST}" PGPORT="${PG_PORT}" POSTGRES_USER="${PG_DRILL_USER}" POSTGRES_DB="${SOURCE_DB}" RESTORE_TARGET_DB="${TARGET_DB}" PGPASSWORD="${PG_DRILL_PASS}" \
  sh "${SCRIPT_DIR}/restore.sh" "${BACKUP_FILE}"

# Verify restored row counts and schema invariants on TARGET_DB
TENANTS_COUNT="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM tenants;")"
USERS_COUNT="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM users;")"
AGENTS_COUNT="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM agents;")"
CALLS_COUNT="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM calls;")"
TURNS_COUNT="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM turns;")"
LATENCY_COUNT="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM call_latency_stats;")"
RESTORED_TABLES="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public';")"
RESTORED_REV="$(psql "${TARGET_PG_URL}" -Atc "SELECT version_num FROM alembic_version LIMIT 1;")"
PCAP_EXISTS="$(psql "${TARGET_PG_URL}" -Atc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='pcap_artifacts';")"

T_RESTORE_VERIFIED_NS="$(date +%s%N)"
T_RESTORE_VERIFIED_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ "${TENANTS_COUNT}" != "1" || "${USERS_COUNT}" != "1" || "${AGENTS_COUNT}" != "1" || "${CALLS_COUNT}" != "2" || "${TURNS_COUNT}" != "4" || "${LATENCY_COUNT}" != "2" ]]; then
  echo "[dr-drill] ERROR: Row count mismatch after restore!" >&2
  exit 1
fi

if [[ "${PCAP_EXISTS}" != "0" ]]; then
  echo "[dr-drill] ERROR: pcap_artifacts table should not exist at revision ${RESTORED_REV}" >&2
  exit 1
fi

BACKUP_BYTES="$(stat -c '%s' "${BACKUP_FILE}")"
BACKUP_SHA256="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"

python3 - <<PY
import json
from pathlib import Path

t_last_write_ns = int("${T_LAST_WRITE_NS}")
t_backup_start_ns = int("${T_BACKUP_START_NS}")
t_backup_end_ns = int("${T_BACKUP_END_NS}")
t_verify_start_ns = int("${T_VERIFY_START_NS}")
t_verify_end_ns = int("${T_VERIFY_END_NS}")
t_disaster_start_ns = int("${T_DISASTER_START_NS}")
t_restore_verified_ns = int("${T_RESTORE_VERIFIED_NS}")

rpo_seconds = round((t_backup_end_ns - t_last_write_ns) / 1e9, 4)
backup_duration_seconds = round((t_backup_end_ns - t_backup_start_ns) / 1e9, 4)
verify_duration_seconds = round((t_verify_end_ns - t_verify_start_ns) / 1e9, 4)
rto_seconds = round((t_restore_verified_ns - t_disaster_start_ns) / 1e9, 4)

report = {
    "drill_status": "PASS",
    "executed_at_utc": "${T_RESTORE_VERIFIED_ISO}",
    "last_write_utc": "${T_LAST_WRITE_ISO}",
    "disaster_declared_utc": "${T_DISASTER_ISO}",
    "restore_verified_utc": "${T_RESTORE_VERIFIED_ISO}",
    "alembic_revision_source": "${ALEMBIC_REV}",
    "alembic_revision_restored": "${RESTORED_REV}",
    "restored_public_tables": int("${RESTORED_TABLES}"),
    "backup_file": "${BACKUP_FILE}",
    "backup_size_bytes": int("${BACKUP_BYTES}"),
    "backup_sha256": "${BACKUP_SHA256}",
    "metrics": {
        "measured_rpo_seconds": rpo_seconds,
        "measured_rto_seconds": rto_seconds,
        "backup_duration_seconds": backup_duration_seconds,
        "backup_verify_duration_seconds": verify_duration_seconds,
        "rpo_data_loss_rows": 0,
    },
    "verified_row_counts": {
        "tenants": int("${TENANTS_COUNT}"),
        "users": int("${USERS_COUNT}"),
        "agents": int("${AGENTS_COUNT}"),
        "calls": int("${CALLS_COUNT}"),
        "turns": int("${TURNS_COUNT}"),
        "call_latency_stats": int("${LATENCY_COUNT}"),
        "pcap_artifacts_table_present": bool(int("${PCAP_EXISTS}")),
    },
}

out_path = Path("${REPORT_FILE}")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
PY

echo "[dr-drill] DR drill completed successfully. Report written to ${REPORT_FILE}"
