# VoxDesk final package — 2026-11-03

## What is in this zip
The `voxdesk-call` tree with the datetime/`as_utc` patches, SPA route, restore.sh,
SIP readyz `status` field, live-test env wiring, local SIEM receivers, and
unshallow git history (24 commits, no rewrite).

## Verified in this sandbox
- Pytest file-run of 618 files: product failures fixed (swaps, CRM retry_after,
  recovery, identity PG enum, tracked artifacts, Redis webhook/QA/telephony).
- Local live: PostgreSQL 17 + Alembic `0069`, Redis, Helm, SIP gateway,
  syslog CEF, Splunk HEC, Elastic `_bulk`, Datadog intake, audit Ed25519.
- B1 image: `voxdesk-api:check` (`0a6f816873f1`, 2.07GB) — not inside this zip.
- T6: `git fetch --unshallow` from `https://github.com/Ainul-550Islam/voxdesk-call-ainul-islam.git`.
  SHAs in `docs/DUE_DILIGENCE/GIT_HISTORY.md` are present. History was not rewritten.

## Not claimed
Paid/external live tests still skip without operator keys (Twilio, Telnyx,
Vonage, OpenAI/Gemini, CRM sandboxes, cloud KMS, FCM/APNs, custom-domain TLS,
public HTTPS webhook). Keys were not faked.

## How to re-run local live tests
```
set -a; . scripts/live_local.env; set +a
# start redis, postgres, sip-gateway, syslog :6514, scripts/local_siem_receivers.py
python -m pytest tests/jobs/test_recovery.py tests/test_identity_pg_enum.py \
  tests/telephony/test_live_sip_trunk.py tests/compliance_v2/test_live_*.py
```
