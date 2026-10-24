# Running the load tests

Two harnesses, two questions:

| Question | Harness | What it touches |
|---|---|---|
| How many concurrent realtime sessions does the edge hold, and what does it cost in CPU/RSS? | `loadtest/gateway_ramp.py` | real `gateway-go` WebSocket + ingest, real `media-engine-rs` control plane and UDP RTP |
| How does the browser path behave under a ramp? | `loadtest/k6_web_call.js` | real `gateway-go` sessions with real access tokens |
| How many concurrent voice calls does one API worker serve? | `loadtest/voice_capacity_test.py` | real `/telephony/ws` handler with `provider_fakes` STT/LLM/TTS (see that file's docstring) |

Nothing here bypasses authentication, and nothing hard-codes a result. Every
number in the JSON report is either measured on the wire or read from `/proc`,
`docker stats`, or a service's own `/metrics`.

## Safety first

* Loopback targets are always allowed. **Any remote target requires
  `LOADTEST_ALLOW_REMOTE=1`** (`loadtest/safety.py`), because a load test aimed at
  production is a production outage.
* `gateway-go` fails closed without `VOXDESK_GATEWAY_JWT_SECRET` and
  `VOXDESK_GATEWAY_INGEST_SECRET`. Generate real ones; placeholders are rejected
  at boot.
* The ramp mints a real access token, so it needs a real tenant and user id. It
  does not create one, because inventing principals in a tenant is not a
  load-testing concern.

## 1. Bring up the stack

```sh
docker compose -f docker-compose.liveproof.yml up -d gateway media-engine
docker compose -f docker-compose.liveproof.yml ps
curl -s localhost:8790/healthz
curl -s localhost:8791/healthz
```

The compose file fixes the dev secrets, mounts nothing from the host, and binds
the services to loopback only (`127.0.0.1:8790`, `127.0.0.1:8791`).

## 2. Gateway + media-engine ramp

```sh
export VOXDESK_GATEWAY_INGEST_SECRET=dev-ingest-secret-0123456789
export VOXDESK_LOADTEST_TENANT_ID=$(uuidgen)
export VOXDESK_LOADTEST_USER_ID=$(uuidgen)

python loadtest/gateway_ramp.py \
  --ramp 10,25,50,100,200,500 \
  --step-seconds 10 \
  --publish-hz 2 \
  --engine-url http://127.0.0.1:8791 \
  --audio-sessions 10 \
  --output reports/loadtest/gateway_ramp.json
```

What you get, per step:

* `connect_ms`, `ready_ms`, `subscribed_ms` — p50/p95/p99 of the real handshake;
* `pong_rtt_ms` — p50/p95/p99 and jitter (population stdev of consecutive RTTs);
* `ping_drop_pct` — pings whose pong never arrived before the next deadline: this
  is the honest definition of loss on a heartbeat protocol, and it is `null` when
  no ping was sent rather than a fake zero;
* `delivery_latency_ms` and `delivery_drop_pct` — measured only when
  `--publish-hz` publishes real ingest events, because a gateway with no publisher
  delivers nothing;
* `audio.frames_sent` — real 8 kHz G.711 µ-law RTP, 160-byte payloads, 20 ms
  cadence, correct sequence/timestamp, sent to the engine's own UDP candidate;
* `gateway_stats` / `engine_stats` — CPU seconds and RSS read from `/proc` (or
  `docker stats` with `VOXDESK_LOADTEST_GATEWAY_CONTAINER=...`), plus the CPU delta
  across the step. When a process cannot be found the block says
  `available: false` and why; it never estimates.

Exit code is non-zero when any step's ping drop reaches 5 %.

### Container stats

```sh
export VOXDESK_LOADTEST_GATEWAY_CONTAINER=voxdesk-gateway
export VOXDESK_LOADTEST_MEDIA-ENGINE-RS_CONTAINER=voxdesk-media-engine
```

(Use the container name in `VOXDESK_LOADTEST_<SERVICE>_CONTAINER`; the default
service labels are `gateway-go` and `media-engine-rs`.)

## 3. Browser-path ramp with k6

```sh
K6_ACCESS_TOKEN="$(python - <<'PY'
import uuid
from app.auth.jwt import create_access_token
token, _ = create_access_token(
    user_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
    tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    role="owner",
    token_version=1,
)
print(token)
PY
)" \
BASE_WS=ws://127.0.0.1:8790/ws \
k6 run loadtest/k6_web_call.js
```

Thresholds: `ws_connecting p95 < 1500 ms`, `gw_ready_ms p95 < 1000 ms`,
`gw_rtt_ms p95 < 400 ms`, `gw_ready_ok > 98 %`, fewer than 5 dropped iterations.
A failed threshold is a real regression in the edge, not a flaky number: the
script fails closed when a token is missing rather than measuring an
unauthenticated socket.

## 4. Voice capacity (single API worker)

```sh
python loadtest/voice_capacity_test.py --steps 10,20,30,40,50,60
```

The knee point recorded in `docs/CAPACITY_MODEL.md` comes from this harness. It
uses recorded caller audio and fakes **only** the external STT/LLM/TTS network
calls; the FastAPI WebSocket handler, stream-token verification, G.711 codec,
denoise filter, `LatencyObserver` and database writes are the real ones. If you
need real providers, use the live proof runbook (`docs/LIVE_PROOF_RUNBOOK.md`)
instead — that path spends real money on purpose and is capped.

## Interpreting the report

* A step where `connected < concurrent_sessions` is a hard failure: the edge did
  not accept the connections. Look at `errors` and the gateway's own `error`
  frames before blaming the harness.
* Jitter that grows while RTT stays flat means scheduling pressure on the client
  side; check the harness host's own CPU before concluding anything about the
  server.
* `delivery_drop_pct == null` means no publisher was configured. It does not mean
  zero loss.
* Keep the JSON: `docs/CAPACITY_MODEL.md` is regenerated from these runs, and a
  capacity claim without a report beside it is a guess.
