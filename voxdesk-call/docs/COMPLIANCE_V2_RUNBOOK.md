# Compliance v2 runbook

Apply, migrate, verify, and operate the Batch 03 control plane. Commands and
environment only.

## Apply

```bash
unzip voxdesk-batch03-patch.zip -d voxdesk-call
cd voxdesk-call
./apply_batch03.sh            # requires alembic/versions/0064_carrier_s2s.py
python3 -m alembic upgrade head
python3 -m pytest tests/compliance_v2 tests/security_v2 -q
make compliance-v2-verify
```

`apply_batch03.sh` is idempotent (marker-guarded), refuses to run when the
parent revision is absent, and regenerates `tests/truth/routes_snapshot.json`
and `contracts/openapi.json` from the mounted application.

## Environment

```bash
# SIEM (SIEM_ENABLED=false disables the dispatcher loop and nothing else)
SIEM_ENABLED=true
SIEM_BATCH_SIZE=200
SIEM_MAX_ATTEMPTS=5
SIEM_CIRCUIT_THRESHOLD=3
SIEM_CIRCUIT_COOLDOWN_SECONDS=300
SIEM_HTTP_TIMEOUT_SECONDS=10
SIEM_SPLUNK_CHANNEL=voxdesk-audit

# KMS (local_dev refuses to boot with APP_ENV=production)
KMS_PROVIDER=aws|gcp|azure|local_dev
KMS_KEY_ID=
KMS_REGION=
KMS_AZURE_VAULT_URL=
KMS_LOCAL_MASTER_KEY_PATH=var/compliance/local_master.key
KMS_MAX_ATTEMPTS=4
KMS_REKEY_BATCH_SIZE=100

# Data residency
DATA_REGIONS=us-east-1,eu-west-1
DEFAULT_REGION=us-east-1

# Audit signing + evidence
AUDIT_SIGNING_KEY_PATH=var/compliance/audit_signing.pem
COMPLIANCE_EVIDENCE_DIR=var/compliance/evidence
```

## Migrate and roll back

```bash
python3 -m alembic upgrade 0065_compliance_v2
python3 -m alembic downgrade 0064_carrier_s2s
make compliance-v2-migration-check
```

## Scheduler

```bash
python3 -m scripts.scheduler
```

Loops (intervals in `app/compliance_v2/jobs.py`): `siem_dispatch` 30s,
`dsar_tick` 300s, `breach_clock` 300s, `access_review` 3600s,
`break_glass_expiry` 300s, `session_logoff` 600s, `evidence_daily` 86400s.

## Operator commands

```bash
# SIEM health and dead letters
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/siem/status"
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/siem/dead-letters"

# Audit chain verification and signed export
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/audit/verify"
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $KEY" \
  "$API/api/tenants/$TENANT/compliance/audit/exports"
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/audit/exports/$EXPORT_ID/download" -o bundle.zip

# KMS health and a rekey
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/kms/health"
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $KEY" \
  "$API/api/tenants/$TENANT/compliance/kms/rekey" -H 'Content-Type: application/json' \
  -d '{"dry_run": true}'

# Residency pin and check
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/residency/pin" -H 'Content-Type: application/json' \
  -d '{"region": "eu-west-1", "justification": "customer contract"}'
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/residency/check"

# Evidence
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $KEY" \
  "$API/api/tenants/$TENANT/compliance/evidence/runs"
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/evidence/latest"

# Break-glass and DSAR
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "$API/api/tenants/$TENANT/compliance/hipaa/break-glass" -H 'Content-Type: application/json' \
  -d '{"scopes": {"resource_types": ["transcript"]}, "reason": "patient emergency", "minutes": 30}'
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $KEY" \
  "$API/api/tenants/$TENANT/compliance/dsar/requests" -H 'Content-Type: application/json' \
  -d '{"kind": "export", "subject_type": "contact", "subject_id": "..."}'
```

## Security harness

```bash
make compliance-v2-harness
python3 -m security_harness.runner --app --strict --evidence-dir evidence/security
cat evidence/security/index.json
```

Exit codes: `0` clean, `1` FAIL, `2` REVIEW under `--strict`. `SKIP` means the
probe could not run in this environment; it is never a pass.

## Live provider verification

Every test in `tests/compliance_v2/test_live_providers.py` is marked `live` and
skips unless its credentials are exported.

```bash
export VOXDESK_LIVE_AWS_KMS_KEY_ID= VOXDESK_LIVE_AWS_KMS_REGION=
export VOXDESK_LIVE_GCP_KMS_KEY_NAME= VOXDESK_LIVE_AZURE_VAULT_URL= VOXDESK_LIVE_AZURE_KEY_NAME=
export VOXDESK_LIVE_SPLUNK_HEC_URL= VOXDESK_LIVE_SPLUNK_HEC_TOKEN= VOXDESK_LIVE_SPLUNK_INDEX=
export VOXDESK_LIVE_DATADOG_API_KEY= VOXDESK_LIVE_DATADOG_SITE=us1
export VOXDESK_LIVE_ELASTIC_URL= VOXDESK_LIVE_ELASTIC_INDEX= VOXDESK_LIVE_ELASTIC_API_KEY=
export VOXDESK_LIVE_SYSLOG_HOST= VOXDESK_LIVE_SYSLOG_PORT=6514 VOXDESK_LIVE_SYSLOG_TLS=1
export VOXDESK_LIVE_S3_BUCKET= VOXDESK_LIVE_S3_REGION= VOXDESK_LIVE_S3_PREFIX=
python3 -m pytest tests/compliance_v2/test_live_providers.py -q
```

## Evidence retention

`COMPLIANCE_EVIDENCE_DIR` holds `evidence/soc2/<tenant>/<run_key>/` snapshots and
`evidence/security/security-harness-<stamp>.json` reports with a digest index in
`evidence/security/index.json`. Back up the directory; nothing in it is
regenerable byte-for-byte.
