# Carrier media and speech-to-speech runbook

Commands and environment only. Every status is read from the deployment.

## Environment

| Variable | Used by | Meaning |
| --- | --- | --- |
| `VONAGE_API_KEY` / `VONAGE_API_SECRET` | api | Vonage account credentials |
| `VONAGE_APPLICATION_ID` | api | Vonage voice application id |
| `VONAGE_PRIVATE_KEY_PATH` / `VONAGE_PRIVATE_KEY` | api | RS256 private key (path or PEM) |
| `VONAGE_SIGNATURE_SECRET` | api | Inbound webhook signature secret |
| `SIP_GATEWAY_URL` / `SIP_GATEWAY_TOKEN` | api | Gateway control API and its shared secret |
| `VOXDESK_SIP_GATEWAY_TOKEN` | gateway | The same secret, from the gateway's side |
| `VOXDESK_SIP_MEDIA_WS_URL` | gateway | `ws://…/telephony/sip/ws` of the application |
| `VOXDESK_SIP_HOST` / `VOXDESK_SIP_PORT` | gateway | Host and port advertised in SDP |
| `VOXDESK_SIP_CODEC` | gateway | `PCMU` or `PCMA` |
| `VOXDESK_SIP_CONFIG_URL` / `VOXDESK_SIP_CONFIG_TTL` | gateway | Trunk configuration endpoint and cache TTL |
| `VOXDESK_SIP_ALLOWED_CIDRS` | gateway | Inbound CIDR allowlist (empty = allow all) |
| `OPENAI_REALTIME_MODEL` / `GEMINI_LIVE_MODEL` | api | Realtime model ids |
| `ENABLE_S2S` / `S2S_FALLBACK_ENABLED` | api | Speech-to-speech runtime and its cascaded fallback |
| `S2S_MAX_SESSION_SECONDS` / `S2S_MAX_TOOL_CALLS` | api | Ceilings of one session and one call |
| `S2S_SESSION_BUDGET_MICRO_USD` / `S2S_TENANT_BUDGET_MICRO_USD` | api | Cost ceilings of one session and of a tenant |

## Commands

```bash
make carriers-capabilities   # capability matrix of this deployment
make carriers-live-test      # live credential checks (needs the credentials)
make s2s-smoke-live          # speech-to-speech smoke test (needs a provider key)
make sip-gateway-build       # build the Go gateway
make sip-gateway-test        # go test -race in services/sip-gateway
make sip-gateway-run         # run the gateway locally

docker compose -f infra/docker/docker-compose.sip.yml up --build

alembic upgrade head && alembic heads
make verify-truth
ruff check app tests scripts loadtest
```

## Endpoints

- `GET`/`POST /telephony/vonage/answer` — NCCO with the media WebSocket action
- `POST /telephony/vonage/events` — signed Vonage event webhook
- `WS /telephony/vonage/ws` — Vonage media: L16 16 kHz binary frames
- `WS /telephony/sip/ws` — SIP media: binary G.711 frames, JSON control events
- `GET /api/v1/carriers/capabilities` — capability matrix
- `POST /api/v1/carriers/{carrier}/test-connection` — credential check (`Idempotency-Key` required)
- `GET /api/v1/s2s/sessions`, `GET /api/v1/s2s/sessions/{id}` — speech-to-speech sessions

## Gateway control API

`VOXDESK_SIP_API_ADDR`; the bearer token is required on every route except
`/healthz`, `/readyz` and `/metrics`.

```bash
curl -H "Authorization: Bearer $SIP_GATEWAY_TOKEN" "$SIP_GATEWAY_URL/v1/calls"

curl -X POST -H "Authorization: Bearer $SIP_GATEWAY_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"from":"+15550001111","to":"sip:peer@example.com","codec":"PCMU"}' \
     "$SIP_GATEWAY_URL/v1/calls"

curl -X POST -H "Authorization: Bearer $SIP_GATEWAY_TOKEN" \
     -H "Content-Type: application/json" -d '{"digits":"5"}' \
     "$SIP_GATEWAY_URL/v1/calls/<call-id>/dtmf"

curl -X POST -H "Authorization: Bearer $SIP_GATEWAY_TOKEN" \
     "$SIP_GATEWAY_URL/v1/calls/<call-id>/hangup"
```

## Media protocol on `/telephony/sip/ws`

The socket presents `Authorization: Bearer <token>` and the per-call session
token `X-VoxDesk-SIP-Session`, which is
`HMAC-SHA256(token, "{call_id}:{timestamp}:{nonce}")`.

```
-> {"type":"start","call_id":"…","from":"+1…","to":"+1…","codec":"PCMU"}
-> binary frame: the raw G.711 audio of the call
-> {"type":"dtmf","digit":"5"}
<- binary frame: the raw G.711 audio the gateway plays out
```

## Metrics

`voxdesk_s2s_failover_total{reason}`, `voxdesk_sip_calls_active`,
`voxdesk_sip_calls_total`, `voxdesk_sip_rtp_packets_in_total`,
`voxdesk_sip_rtp_packets_out_total`, `voxdesk_sip_rtp_loss_total`,
`voxdesk_sip_jitter_ms`, `voxdesk_sip_setup_time_seconds`,
`voxdesk_sip_responses_total{code}`, `voxdesk_sip_media_frames_total{direction}`,
`voxdesk_sip_dtmf_events_total{direction}`, `voxdesk_sip_sessions_ended_total`.

## Rollback

```bash
alembic downgrade 0063_live_proof_runs
helm upgrade voxdesk infra/helm/voxdesk --set sipGateway.enabled=false
```
