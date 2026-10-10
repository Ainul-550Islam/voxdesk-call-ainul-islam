# SELL CHECK 3 of 4 — Docker run check

Copy the whole block below with one click and paste it into the coding agent. It contains the mission, the check rules, the file tree with a `# comment` beside every file, the exact commands and the pass criteria.

```text
# ======================================================================================================================
# SELL CHECK 3 of 4 — DOCKER RUN CHECK   (measurement prompt: run it, record the truth, do not fix product code)
# Series: VoxDesk x Retell sell-ready gap closure | repo root: voxdesk-call/ | companion to SELL PROMPT 1-10
# PREREQUISITE: CHECK 1 done. Docker + Compose v2 available; >= 8 GB RAM and >= 20 GB disk recommended.
# MISSION: Prove the stack really runs in containers: compose files validate, images build, containers become healthy
#   with a generated test env, the real entrypoint migrates, health/readiness respond, a signed Twilio voice webhook
#   returns TwiML, a Twilio-format media-stream WebSocket is accepted and closes cleanly without provider keys,
#   gateway/media engine/Prometheus/Grafana are healthy, backup/restore works, then everything is torn down. No real
#   provider is contacted.
# RUN ORDER: CHECK 1 (backend) -> CHECK 2 (frontend) -> CHECK 3 (docker run) -> CHECK 4 (other + summary) -> then SELL
#   PROMPT 1. After EVERY SELL PROMPT re-run `make check-all` and compare with the baseline in reports/check/SUMMARY.md:
#   any new failure is a regression the prompt must fix before it is accepted.
# CHECK RULES (apply to every step)
#   C1  This is a MEASUREMENT prompt. Do NOT change product code, existing tests, migrations, compose files or docs
#       to make anything pass. Add only the harness files marked [NEW]/[MODIFY] in the tree.
#   C2  Record every failure verbatim: command, exit code, last 40 output lines. Never write "environment issue"
#       without proof (show the missing tool, key, port or RAM).
#   C3  Triage every failure as PRODUCT (real defect) | ENV (missing tool/key/port/RAM) | FLAKY (passes on rerun:
#       say how many reruns) | SKIPPED (not run: say why). SKIPPED is never counted as passed.
#   C4  No real phone calls, charges, bookings or CRM mutations. Tests marked real_provider run ONLY if
#       VOXDESK_REAL_INTEGRATION=1 AND the operator supplied credentials; otherwise report SKIPPED.
#   C5  Be memory-safe. The ~4,048-test suite was killed (exit 137 / -9) in a previous environment: run in chunks,
#       one process per chunk, retry an OOM-killed chunk with half the size and record the event.
#   C6  Never print or commit secrets. Generated env files are git-ignored and written with mode 0600; reports show
#       variable NAMES only.
#   C7  Every number in a report comes from a command you ran in THIS session; paste the command next to the number.
#       No estimates, no "should pass".
#   C8  Output the COMPLETE content of every script/test you create. Never write "...", "rest unchanged" or "omitted
#       for brevity".
#   C9  Write reports/check/<AREA>.md (human) and reports/check/<AREA>.json (a list of {step, command, exit_code,
#       duration_s, status, counts, class}). status = PASS | FAIL | SKIPPED; class = PRODUCT | ENV | FLAKY | "".
#   C10 Tags in the tree: [NEW] create | [MODIFY] change only what is described | [KEEP] existing file: run/read it,
#       do not edit | [VERIFY] read-only inspection, record findings.
# COMMENT FORMAT: # [TAG] kind — what the file contains / what to do with it. Commands below are plain lines; lines
#   starting with # are explanations.
# ======================================================================================================================

voxdesk-call/
├── app/
│   └── telephony/
│       ├── stream_auth.py       # [VERIFY] module — read-only: X-Twilio-Signature validation
│       │                        #   (twilio.request_validator.RequestValidator with TWILIO_AUTH_TOKEN) and the media-stream
│       │                        #   token; the smoke harness must use the same algorithm
│       └── twilio_handler.py    # [VERIFY] module — read-only: POST /telephony/voice, /ivr, /outbound-answer, /status,
│                                #   /transfer-status and WebSocket /telephony/ws (prefix /telephony). Read /voice to learn
│                                #   the form fields and the dialed-number -> tenant lookup, then build the signed smoke
│                                #   request accordingly; read the /ws handler for the stream-token format
├── scripts/
│   ├── backup.sh                # [KEEP] script — existing backup: read the usage header, run against the check stack only
│   │                            #   (step 10)
│   ├── backup_verify.sh         # [KEEP] script — existing backup verification: run after backup.sh (step 10)
│   ├── check_docker.sh          # [NEW] script — orchestrates steps 1-14 below; a `trap` ALWAYS runs the teardown (compose
│   │                            #   down -v --remove-orphans) even after a crash; writes reports/check/docker.json +
│   │                            #   docker.md; never touches a compose project other than voxdesk-check / voxdesk-check-dev
│   ├── create_owner.py          # [KEEP] script — creates an owner user: read its usage header, use it for the auth step
│   ├── make_check_env.py        # [NEW] script — builds .env.check from .env.example + the `${VAR:?}` variables of
│   │                            #   docker-compose.prod.yml with random secrets (JWT_SECRET, REALTIME_GATEWAY_INGEST_SECRET
│   │                            #   >= 16 chars, passwords), APP_ENV=staging, TRUSTED_HOSTS=localhost,127.0.0.1,api,
│   │                            #   MEDIA_ENGINE_PUBLIC_IP=127.0.0.1, provider keys blank; refuses to run when
│   │                            #   APP_ENV=production; writes mode 0600; prints variable NAMES only
│   ├── restore.sh               # [KEEP] script — existing restore: run into a fresh database of the check stack only (step
│   │                            #   10)
│   ├── seed_demo_tenant.py      # [KEEP] script — creates a demo tenant (`python -m scripts.seed_demo_tenant`): used by the
│   │                            #   auth step
│   ├── smoke_test.py            # [KEEP] script — existing read-only deployment smoke (/health, /health/ready, /, /metrics,
│   │                            #   /auth/login rejects bogus login with 4xx): run it first with SMOKE_BASE_URL, then record
│   │                            #   what stack_smoke.py adds
│   ├── stack_smoke.py           # [NEW] script — httpx + websockets harness with subcommands: wait (poll `docker compose ps
│   │                            #   --format json` until all services healthy or timeout, print the last 80 log lines of
│   │                            #   every unhealthy service), http (health / health/ready / health/dependencies / openapi /
│   │                            #   metrics), auth (seed tenant + owner, login, tenant-scoped call returns 200, second tenant
│   │                            #   gets 404 for the first tenant's object), twilio (signed /telephony/voice -> TwiML
│   │                            #   containing <Stream>; bad signature -> 403), ws (Twilio-format media-stream handshake:
│   │                            #   connected, start, 5 x 160-byte mu-law silence frames, stop; expect accepted upgrade and a
│   │                            #   CLEAN close, never a worker crash), gateway (healthz), observability (Prometheus targets
│   │                            #   up, Grafana /api/health)
│   ├── staging_certify.py       # [KEEP] script — existing orchestrator: use ONLY the read-only modes --preflight and
│   │                            #   --observability; never --compose-up on anything except a staging identity (read its
│   │                            #   guards first)
│   └── twilio_signature.py      # [NEW] script — computes X-Twilio-Signature (HMAC-SHA1 over full URL + sorted POST params,
│                                #   base64) for the smoke webhook calls; unit-tested against the vector in Twilio's published
│                                #   algorithm
├── tests/
│   └── smoke/
│       └── test_stack_smoke.py  # [NEW] test — pytest wrapper (marker `docker`, skipped unless VOXDESK_STACK_URL is set)
│                                #   running the same assertions as stack_smoke.py against a running stack
├── .env.check.example           # [NEW] config — documented template of the generated check environment (names + comments
│                                #   only, no secrets): every variable required by docker-compose.prod.yml
│                                #   (CONDUCTOR_WEBHOOK_SECRET, GRAFANA_ADMIN_PASSWORD, JWT_SECRET, MEDIA_ENGINE_PUBLIC_IP,
│                                #   POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER, REALTIME_GATEWAY_INGEST_SECRET,
│                                #   TRUSTED_HOSTS) plus the app variables from .env.example
├── Caddyfile                    # [KEEP] config — TLS front: needs a public hostname for ACME; step 4 starts the stack
│                                #   WITHOUT caddy and tests it separately only if a local `tls internal` config is available
├── docker-compose.check.yml     # [NEW] config — override loaded ONLY by this prompt (`-f docker-compose.prod.yml -f
│                                #   docker-compose.check.yml`): `restart: "no"`, caddy moved to profile `tls-check` so the
│                                #   stack starts without public ACME, no extra host ports; never referenced by deploy
│                                #   scripts, staging or production
├── docker-compose.full.yml      # [KEEP] config — builds the generated filler engines (removed in PART 0): record whether
│                                #   it builds TODAY; either result is a finding
├── docker-compose.prod.yml      # [KEEP] config — PRODUCTION stack (db, redis, api, realtime-gateway, media-engine,
│                                #   scheduler, backup, prometheus, grafana, caddy): exercised, not edited. Published: api
│                                #   127.0.0.1:8000, gateway 127.0.0.1:8790, grafana 127.0.0.1:3000, media 5000/udp, caddy
│                                #   80/443
├── docker-compose.staging.yml   # [KEEP] config — staging overrides: validated with `config -q`, not edited
├── docker-compose.yml           # [KEEP] config — DEV stack (db, api, scheduler, dashboard on node:20-alpine :5173):
│                                #   exercised in step 12, not edited
├── Dockerfile                   # [KEEP] config — API image (node:20-alpine dashboard build stage +
│                                #   python:3.12-slim-bookworm, HEALTHCHECK, ENTRYPOINT scripts/entrypoint.sh): built in step
│                                #   4, not edited
├── Makefile                     # [MODIFY] config — add `check-docker` (calls scripts/check_docker.sh); do not change `up`
│                                #   / `down`
└── pytest.ini                   # [MODIFY] config — register ONE new marker `docker` (needs a running stack); keep unit,
                                 #   integration, real_provider and asyncio_mode untouched

# ======================================================================================================================
# COMMANDS — run in this order from the repo root; record exit code + seconds for each
# ======================================================================================================================
# --- STEP 1: preflight ---
docker --version && docker compose version
free -m; df -h .                                    # >= 8 GB RAM and >= 20 GB disk recommended (the Rust and Go images compile in Docker); record

# --- STEP 2: validate every compose file (starts nothing) ---
docker compose -f docker-compose.yml config -q
docker compose -f docker-compose.prod.yml --env-file .env.check config -q
docker compose -f docker-compose.staging.yml --env-file .env.check config -q
docker compose -f docker-compose.full.yml config -q            # record: valid? (it builds the filler engines)

# --- STEP 3: generated environment (never committed) ---
python scripts/make_check_env.py --out .env.check
export DC="docker compose -p voxdesk-check -f docker-compose.prod.yml -f docker-compose.check.yml --env-file .env.check"

# --- STEP 4: build (record seconds per image and sizes) ---
$DC build --progress=plain 2>&1 | tee reports/check/docker_build.log
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep voxdesk-check

# --- STEP 5: start and wait for health ---
$DC up -d
python scripts/stack_smoke.py wait --project voxdesk-check --timeout 300
$DC ps

# --- STEP 6: HTTP smoke (existing script first) ---
SMOKE_BASE_URL=http://127.0.0.1:8000 python scripts/smoke_test.py
python scripts/stack_smoke.py http --base http://127.0.0.1:8000
$DC exec -T api alembic current                     # must equal `alembic heads`: the entrypoint migrated under the advisory lock

# --- STEP 7: auth + tenancy ---
$DC exec -T api python -m scripts.seed_demo_tenant
python scripts/stack_smoke.py auth --base http://127.0.0.1:8000

# --- STEP 8: Twilio webhook + media-stream handshake (no provider is contacted) ---
python scripts/stack_smoke.py twilio --base http://127.0.0.1:8000     # valid signature -> TwiML with <Stream>; bad signature -> 403
python scripts/stack_smoke.py ws --base http://127.0.0.1:8000         # connected/start/media/stop; with blank provider keys it must close CLEANLY (NOT_CONFIGURED), never crash

# --- STEP 9: gateway, media engine, observability ---
python scripts/stack_smoke.py gateway --url http://127.0.0.1:8790/healthz
docker inspect --format '{{json .State.Health}}' $($DC ps -q media-engine)   # compose healthcheck probes :9001/v1/health
python scripts/stack_smoke.py observability --grafana http://127.0.0.1:3000   # Prometheus targets are queried from inside the compose network
python scripts/staging_certify.py --preflight        # read-only modes only
python scripts/staging_certify.py --observability

# --- STEP 10: backup / restore drill (read each script header first) ---
bash scripts/backup.sh && bash scripts/backup_verify.sh && bash scripts/restore.sh        # against the check stack only; record RPO/RTO seconds

# --- STEP 11: log hygiene + resources ---
$DC logs --no-color 2>&1 | grep -Ei "traceback|critical|exception|error" | sort | uniq -c | sort -rn | head -30
docker stats --no-stream

# --- STEP 12: teardown the prod-like stack, then the DEV stack (both bind :8000, so strictly one after the other) ---
$DC down -v --remove-orphans
docker compose -p voxdesk-check-dev -f docker-compose.yml up -d --build
curl -fsS http://127.0.0.1:8000/docs -o /dev/null && curl -fsS http://127.0.0.1:5173 -o /dev/null
docker compose -p voxdesk-check-dev -f docker-compose.yml down -v --remove-orphans

# --- STEP 13: optional image checks (record SKIPPED(tool missing) if absent) ---
hadolint Dockerfile; trivy image --severity HIGH,CRITICAL voxdesk-check-api

# --- STEP 14: prove the teardown ---
docker ps -a --filter name=voxdesk-check; docker volume ls --filter name=voxdesk-check     # both must be empty
bash scripts/check_docker.sh                         # the orchestrator runs 1-14 with the teardown trap and writes reports/check/docker.md + docker.json

# ======================================================================================================================
# PASS CRITERIA AND REPORT
# ======================================================================================================================
# PASS WHEN (record the real value for each, pass or fail): every compose file validates | images build (seconds + size
#   per image) | every service healthy within 300 s | alembic current = head after the real entrypoint | /health = 200 and
#   /health/ready = 200 | existing smoke_test.py passes | tenant-scoped call = 200 and cross-tenant = 404 | signed
#   /telephony/voice returns TwiML with <Stream>, bad signature = 403 | media-stream handshake accepted and closed cleanly
#   with blank provider keys | gateway and media engine healthy | Prometheus targets up and Grafana healthy | backup +
#   verify + restore succeed | no unexpected Traceback/CRITICAL lines at idle | dev stack answers /docs and :5173 |
#   teardown leaves no containers or volumes.
# REPORT reports/check/DOCKER_CHECK.md: host resources, step table, image sizes and build seconds, per-service health,
#   smoke results, log-hygiene table (message | count | service), backup/restore timings, teardown proof. Real
#   Twilio/Deepgram/ElevenLabs/LLM calls are out of scope (C4).

# NEXT: CHECK 4 - OTHER CHECKS AND SUMMARY (SELL_CHECK_04_Other_and_Summary.md).
```
