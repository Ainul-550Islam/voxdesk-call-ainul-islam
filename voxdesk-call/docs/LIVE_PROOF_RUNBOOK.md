# Live proof runbook

Commands only. Every step either measures something or refuses; nothing here
prints success for a run that did not happen.

## Required environment

```sh
# Telnyx / Twilio media + call control (only the providers you use)
export TWILIO_ACCOUNT_SID=AC... TWILIO_AUTH_TOKEN=... TWILIO_PHONE_NUMBER=+1...
export DEEPGRAM_API_KEY=... ELEVENLABS_API_KEY=... OPENAI_API_KEY=sk-...
export TELNYX_API_KEY=... TELNYX_PUBLIC_KEY=... TELNYX_CONNECTION_ID=...

# Live PSTN proof (refused in production; opt-in)
export LIVE_PROOF_ENABLED=1
export LIVE_PROOF_TENANT_ID=<uuid of the tenant that owns the agent>
export LIVE_PROOF_TEST_NUMBER=+1...      # the agent's own number
export LIVE_PROOF_CALLER_ID=+1...        # number that places the call
export LIVE_PROOF_MAX_MINUTES=3.0 LIVE_PROOF_MAX_USD=0.50
export LIVE_PROOF_EVIDENCE_DIR=evidence/live

# Stack + load target
export DATABASE_URL=postgresql+asyncpg://voxdesk:...@127.0.0.1:5432/voxdesk
export REDIS_URL=redis://127.0.0.1:6379/0
export VOXDESK_GATEWAY_INGEST_SECRET=... VOXDESK_LOADTEST_TENANT_ID=<uuid> VOXDESK_LOADTEST_USER_ID=<uuid>
```

## Stack

```sh
docker compose -f docker-compose.liveproof.yml up -d          # 2 vCPU / 2 GB
LIVEPROOF_CPUS=8 LIVEPROOF_MEM=16g LIVEPROOF_ENGINE_CPUS=8 LIVEPROOF_ENGINE_MEM=16g \
  docker compose -f docker-compose.liveproof.yml up -d        # 8 vCPU / 16 GB
docker compose -f docker-compose.liveproof.yml ps
curl -sf 127.0.0.1:8000/health && curl -sf 127.0.0.1:8790/healthz && curl -sf 127.0.0.1:8791/healthz
DATABASE_URL="$DATABASE_URL" make migrate
```

## Pre-flight (no dial, no spend)

```sh
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/live-proof/readiness | jq
# ready:true  -> every credential and cap is present
# ready:false -> read .problems and .missing_settings; nothing was dialled
```

## PSTN probe (real call, real providers)

```sh
python -m app.telephony.live_proof.pstn_probe \
  --tenant-id "$LIVE_PROOF_TENANT_ID" --minutes 1 --max-usd 0.50 \
  --evidence-dir evidence/live

# or through the API (Idempotency-Key mandatory, owner/admin + CALL_WRITE)
curl -s -X POST localhost:8000/api/live-proof/runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: live-proof-$(date -u +%Y%m%dT%H%M%SZ)" \
  -d '{"max_usd":0.40,"note":"runbook"}' | jq '{id,status,provider_fakes,is_evidence,e2e_p50_ms,e2e_p95_ms,cost_usd}'

curl -s -H "Authorization: Bearer $TOKEN" "localhost:8000/api/live-proof/runs?limit=5" | jq '.runs'
curl -s -H "Authorization: Bearer $TOKEN" "localhost:8000/api/live-proof/runs/$RUN/evidence" \
  | jq '{sha256_matches,provider_fakes,missing_real_stages,stage_metrics}'
curl -s -X POST "localhost:8000/api/live-proof/runs/$RUN/abort" \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: abort-$RUN" | jq '{changed,status:.run.status}'
```

## Gateway + media-engine ramp

```sh
python loadtest/gateway_ramp.py \
  --ws-target 127.0.0.1:8790 --gateway-url http://127.0.0.1:8790 \
  --engine-url http://127.0.0.1:8791 \
  --ramp 10,25,50,100,200,500 --step-seconds 10 --publish-hz 2 \
  --audio-sessions 10 --output evidence/capacity/gateway_ramp_$(date -u +%F).json

K6_ACCESS_TOKEN="$TOKEN" BASE_WS=ws://127.0.0.1:8790/ws k6 run loadtest/k6_web_call.js
```

## Where evidence lands

```
evidence/live/<YYYY-MM-DD>/<run_id>.json   # provider_calls, stage_metrics, turns, recording_sha256, runtime
evidence/live/<YYYY-MM-DD>/<run_id>.csv    # per-turn rows
evidence/capacity/gateway_ramp_<date>.json # per-step connect/ready/rtt/jitter/drop, CPU+RSS, audio frames
```

The JSON is hashed; the SHA-256 lives on the `live_proof_runs` row
(`evidence_sha256`) and is recomputed on read (`sha256_matches`). `provider_fakes`
is computed from the provider ledger: it is false only when every stage
(telephony, stt, llm, tts) has a real recorded call.

## Verify

```sh
make verify-truth && ruff check app tests scripts loadtest && alembic heads
python scripts/verify_contracts.py && python scripts/verify_docs_match_code.py
pytest tests/telephony tests/live_proof tests/truth -q          # offline
pytest tests/test_live_pstn_latency.py -m live -v               # needs the env above
```
