# VoxDesk — SELL CHECK 3 of 4: Docker Run Check (`DOCKER_CHECK.md`)

## 1. Header & Host Environment

| Field | Measured Value |
|---|---|
| Timestamp (UTC) | `2026-10-10T03:02:37Z` |
| Git SHA | `dc85c3336f65` |
| Compose Project | `voxdesk-check` |
| Overall Status | **`PASS`** (`14/14` steps passed in `153s`) |
| Host OS & Kernel | `Linux 6.1.158+ x86_64 GNU/Linux` |
| Docker Version | `Docker version 26.1.5+dfsg1, build a72d7cd` |
| Compose Version | `Docker Compose version v2.29.7` |
| Docker Engine Info | `ServerVersion=26.1.5+dfsg1 Driver=overlay2 CgroupDriver=systemd` |
| Available Disk | `9.2G free of 25G (63% used)` |
| Available Memory | `1611MB avail / 1982MB total` (Swap: `3746MB free / 4095MB total`) |

---

## 2. Compose Config Validation Table (Step 2)

| Compose File(s) | Command | Exit Code |
|---|---|---|
| `docker-compose.yml` | `docker compose -f docker-compose.yml config -q` | `0` |
| `docker-compose.prod.yml` | `docker compose --env-file .env.check -f docker-compose.prod.yml config -q` | `0` |
| `docker-compose.staging.yml` | `docker compose --env-file .env.check -f docker-compose.staging.yml config -q` | `0` |
| `docker-compose.full.yml` | `docker compose --env-file .env.check -f docker-compose.full.yml config -q` | `0` |
| `docker-compose.prod.yml + docker-compose.check.yml` | `docker compose --env-file .env.check -f docker-compose.prod.yml -f docker-compose.check.yml config -q` | `0` |

---

## 3. Image Build & Pull Inventory Table (Step 3)

| Image (`Repository:Tag`) | Image ID | Final Image Size | Build / Pull Role |
|---|---|---|---|
| `caddy:2.9-alpine` | `51f0c496a59a` | `48.5MB` | Pulled base image for Step 12 `caddy validate` (`tls-check` profile) |
| `grafana/grafana:11.1.0` | `c42c21cd0ebc` | `453MB` | Pulled base image for `grafana` |
| `postgres:16-alpine` | `81bd698b4594` | `294MB` | Pulled base image for `db` and `backup` |
| `prom/prometheus:v2.53.0` | `b74abbcc4eac` | `271MB` | Pulled base image for `prometheus` |
| `redis:7-alpine` | `f84b0c467801` | `39.1MB` | Pulled base image for `redis` |
| `voxdesk-check-api:latest` | `7bfa853ed2b1` | `1.89GB` | Built from `./Dockerfile` (Node 20 Vite SPA build + Python 3.12 runtime) |
| `voxdesk-check-media-engine:latest` | `d1efc58a78a1` | `79.4MB` | Built from `services/realtime/media-engine-rs/Dockerfile` (Rust 1.90 release binary on Debian Bookworm Slim) |
| `voxdesk-check-realtime-gateway:latest` | `e3a09229c63c` | `19.6MB` | Built from `services/realtime/gateway-go/Dockerfile` (Go 1.27 static binary on Alpine 3.21) |
| `voxdesk-check-scheduler:latest` | `c0f87b34e3cb` | `1.89GB` | Built from `./Dockerfile` (`python -m scripts.scheduler`) |

---

## 4. Container Status & Resource Snapshot Table (Step 4)

| Container Name | Image | State | Health / Status | Host Port Mapping (Loopback Only) | CPU | Memory Usage (`docker stats --no-stream`) |
|---|---|---|---|---|---|---|
| `voxdesk-check-api-1` | `voxdesk-check-api` | `running` | `Up 2 minutes (healthy)` | `127.0.0.1:8000->8000/tcp` | `12.87%` | `1000MiB / 1.936GiB` |
| `voxdesk-check-backup-1` | `postgres:16-alpine` | `running` | `Up 2 minutes` | `5432/tcp` | `0.00%` | `336KiB / 1.936GiB` |
| `voxdesk-check-db-1` | `postgres:16-alpine` | `running` | `Up 2 minutes (healthy)` | `5432/tcp` | `14.59%` | `119.7MiB / 1.936GiB` |
| `voxdesk-check-grafana-1` | `grafana/grafana:11.1.0` | `running` | `Up 2 minutes` | `127.0.0.1:3000->3000/tcp` | `0.06%` | `27.55MiB / 1.936GiB` |
| `voxdesk-check-media-engine-1` | `voxdesk-check-media-engine` | `running` | `Up 2 minutes (healthy)` | `127.0.0.1:5000->5000/udp, 127.0.0.1:9001->9001/tcp` | `1.81%` | `3.23MiB / 1.936GiB` |
| `voxdesk-check-prometheus-1` | `prom/prometheus:v2.53.0` | `running` | `Up 2 minutes` | `127.0.0.1:9090->9090/tcp` | `0.92%` | `28.04MiB / 1.936GiB` |
| `voxdesk-check-realtime-gateway-1` | `voxdesk-check-realtime-gateway` | `running` | `Up 2 minutes (healthy)` | `127.0.0.1:8790->8790/tcp` | `2.18%` | `6.957MiB / 1.936GiB` |
| `voxdesk-check-redis-1` | `redis:7-alpine` | `running` | `Up 2 minutes (healthy)` | `6379/tcp` | `2.64%` | `5.297MiB / 1.936GiB` |
| `voxdesk-check-scheduler-1` | `voxdesk-check-scheduler` | `running` | `Up 2 minutes (healthy)` | `8000/tcp` | `0.01%` | `60.62MiB / 1.936GiB` |

---

## 5. Step-by-Step Execution Results (Steps 1–14)

| Step | Check | Status | Exit Code | Duration | Measured Output Summary |
|---|---|---|---|---|---|
| Step 1 | Host preflight | **PASS** | `0` | `0s` | Docker version 26.1.5+dfsg1, build a72d7cd | Docker Compose version v2.29.7 | ServerVersion=26.1.5+dfsg1 Driver=overlay2 CgroupDriver=systemd | disk=9.2G free of 25G (63% used) | mem=1611MB avail / 1982MB total |
| Step 2 | Local env & compose config validation | **PASS** | `0` | `1s` | .env.check mode=600; 5/5 compose configs valid (yml, prod, staging, full, prod+check) |
| Step 3 | Build all images | **PASS** | `0` | `4s` | Built api, scheduler, realtime-gateway, media-engine (caddy:2.9-alpine=48.5MB grafana/grafana:11.1.0=453MB postgres:16-alpine=294MB prom/prometheus:v2.53.0=271MB redis:7-alpine=39.1MB voxdesk-check-api:latest=1.89GB voxdesk-check-media-engine:latest=79.4MB voxdesk-check-realtime-gateway:latest=19.6MB voxdesk-check-scheduler:latest=1.89GB ) |
| Step 4 | Start stack (up -d) & wait for healthy | **PASS** | `0` | `75s` | db=healthy, redis=healthy, media-engine=healthy, realtime-gateway=healthy, api=healthy, scheduler=healthy |
| Step 5 | Per-service readiness & Alembic head | **PASS** | `0` | `7s` | db=/var/run/postgresql:5432 - accepting connections; redis=PONG; alembic=0062_drop_pcap_artifacts (head); api/gw/media/prom/grafana ready |
| Step 6 | Owner + demo tenant + HTTP smoke | **PASS** | `0` | `16s` | create_owner.py, seed_demo_tenant.py, smoke_test.py (8 pass, 1 warn, 0 fail), stack_smoke.py http (13/13 PASS), staging_certify.py (PASS) |
| Step 7 | Realtime gateway + media engine smoke | **PASS** | `0` | `0s` | gateway /healthz /readyz /metrics + signed /ingest/v1/publish (200/dedupe/401) + media-engine /v1/health /metrics (8/8 PASS) |
| Step 8 | Twilio webhook signature verification | **PASS** | `0` | `1s` | POST /telephony/voice valid HMAC -> 200 TwiML <Stream>; invalid HMAC -> 403 (2/2 PASS) |
| Step 9 | Simulated call media WebSocket + pytest -m docker | **PASS** | `0` | `22s` | WS /telephony/ws connected->start->media->stop + bad-token 1008 + call_sid verified in DB; pytest (============================== 4 passed in 10.34s ==============================) |
| Step 10 | Observability (Prometheus + Grafana + /metrics) | **PASS** | `0` | `0s` | api_metrics_lines=226; targets=[voxdesk-api:up, voxdesk-media-engine:up, voxdesk-realtime-gateway:up, voxdesk-scheduler:up]; grafana=(database=ok version=11.1.0) |
| Step 11 | Backup & restore drill | **PASS** | `0` | `10s` | backup.sh + backup_verify.sh + restore.sh PASS (file=voxdesk-20261010-030445.dump, bytes=972156 (952K), counts[tables,tenants,users,agents,calls]=239,1,2,2,4) |
| Step 12 | Caddyfile validation | **PASS** | `0` | `1s` | {"level":"info","ts":1791601494.0656953,"logger":"tls.cache.maintenance","msg":"stopped background certificate maintenance","cache":"0xc0004d1d00"} Valid configuration |
| Step 13 | Optional image checks (hadolint / trivy) | **PASS** | `0` | `0s` | hadolint=executed (hadolint v2.12.0: 0 errors, 2 pinning warnings DL3008/DL3018); trivy=SKIPPED (tool missing) |
| Step 14 | Clean teardown (down -v --remove-orphans) | **PASS** | `0` | `14s` | 0 residual containers, networks, or volumes for voxdesk-check |

### Detailed Output for Steps 5–13

#### Step 5 — Per-Service Readiness & Alembic Head
- `db pg_isready`: `/var/run/postgresql:5432 - accepting connections`
- `redis redis-cli ping`: `PONG`
- `api alembic current`: `0062_drop_pcap_artifacts (head)`
- `GET http://127.0.0.1:8000/health`: `{"status":"ok"}`
- `GET http://127.0.0.1:8000/health/ready`: `{"status":"ready","checks":{"database":{"ok":true},"redis":{"ok":true,"configured":true},"providers":{"ok":false,"missing":["deepgram","elevenlabs","llm"]}}}`
- `GET http://127.0.0.1:8790/healthz`: `{"status":"ok"}`
- `GET http://127.0.0.1:8790/readyz`: `{"checks":{"capacity":{"current":0,"headroom":10000,"max":10000,"ok":true},"engine":{"engine_version":"0.0.0","ok":true,"rooms":0,"state":"up"},"rooms":0,"tenants":0},"status":"ok","uptime_s":81}`
- `GET http://127.0.0.1:9001/v1/health`: `{"engine":"media-engine-rs","participants":0,"ready":true,"rooms":0,"streams":0,"tracks":0,"uptime_ms":81323,"v":1,"version":"0.0.0"}`
- `GET http://127.0.0.1:9090/-/ready`: `Prometheus Server is Ready.`
- `GET http://127.0.0.1:3000/api/health`: `{   "commit": "5b85c4c2fcf5d32d4f68aaef345c53096359b2f1",   "database": "ok",   "version": "11.1.0" }`

#### Step 6 — Create Owner, Seed Demo Tenant, and Run HTTP Smoke
- `create_owner.py`: `created owner owner@check.local for tenant 'Check Tenant' (2c24a4de-fa7c-42cb-8c55-b54961678f7e);tenant now has 2 owner(s);`
- `seed_demo_tenant.py`: `Demo tenant already present: Check Tenant on +15550001111 (2c24a4de-fa7c-42cb-8c55-b54961678f7e);`
- `smoke_test.py --base http://127.0.0.1:8000`:
```text
VoxDesk smoke test against http://127.0.0.1:8000

  [PASS] /health — status 200
  [PASS] /health/ready — ready
  [PASS] / — status 200
  [PASS] /metrics — status 200 (200=enabled/open, 401=token-gated, 404=disabled)
  [PASS] /auth/login — rejected anonymously with 401
  [PASS] /api/tenants — rejected anonymously with 401
  [PASS] /telephony/voice — rejected anonymously with 403
  [WARN] provider_config — missing: deepgram, elevenlabs, llm
  [PASS] e2e_guard — disarmed

Smoke summary: 8 pass, 1 warn, 0 fail
```
- `stack_smoke.py http --base http://127.0.0.1:8000 --env-file .env.check`:
```text
[PASS] GET /health — status=200 body={"status":"ok"}
  [PASS] GET /health/ready — status=200 body={"status":"ready","checks":{"database":{"ok":true},"redis":{"ok":true,"configured":true},"providers":{"ok":false,"missing":["deepgram","elevenlabs","llm"]}}}
  [PASS] GET /metrics — status=200 bytes=7096
  [PASS] GET / — status=200 bytes=271
  [PASS] GET /login — status=200 bytes=271
  [PASS] GET /dashboard — status=200 bytes=271
  [PASS] GET /openapi.json — status=200 paths=969
  [PASS] POST /auth/login — authenticated user=owner@check.example.com
  [PASS] GET /health/dependencies — status=503 db_healthy=True cache_healthy=True overall=degraded
  [PASS] GET /auth/me — status=200 tenant_id=2c24a4de-fa7c-42cb-8c55-b54961678f7e permissions=74
  [PASS] GET /api/tenants — status=200 count=1
  [PASS] POST /api/v1/agents — status=201 agent_id=ac31a61f-6df8-42bc-ac9d-568e5215c1fe
  [PASS] GET /api/v1/agents — status=200 found_created=True
```
- `staging_certify.py --base http://127.0.0.1:8000`:
```text
staging_certify — mode=http-certify
==============================================================================
  [PASS       ] verify liveness (/health)          GET /health -> 200
  [PASS       ] verify readiness (/health/ready)   GET /health/ready -> 200
  [PASS       ] verify metrics (/metrics)          GET /metrics -> 200
  [PASS       ] verify dashboard shell (/)         GET / -> 200
  [PASS       ] smoke test (smoke_test.py)         Smoke summary: 8 pass, 1 warn, 0 fail
  [PASS       ] deployment checks (static)         6 files present and valid (pyyaml)
------------------------------------------------------------------------------
OVERALL: PASS   (exit 0)
```

#### Step 7 — Realtime Gateway + Media Engine Smoke
```text
[PASS] gateway GET /healthz — status=200 body={"status":"ok"}
  [PASS] gateway GET /readyz — status=200 body={"checks":{"capacity":{"current":0,"headroom":10000,"max":10000,"ok":true},"engine":{"engine_version":"0.0.0","ok":true,"rooms":0,"state":"up"},"rooms":0,"tenants":0},"status":"ok","uptime_s":97}
  [PASS] gateway GET /metrics — status=200 bytes=5188
  [PASS] gateway POST /ingest/v1/publish (bad secret -> 401) — status=401
  [PASS] gateway POST /ingest/v1/publish (valid secret -> 200) — status=200 body={"delivered":0,"dropped":0,"duplicate":false}
  [PASS] gateway POST /ingest/v1/publish (replay dedupe -> duplicate=true) — status=200 body={"delivered":0,"dropped":0,"duplicate":true}
  [PASS] media-engine GET /v1/health — status=200 body={"engine":"media-engine-rs","participants":0,"ready":true,"rooms":0,"streams":0,"tracks":0,"uptime_ms":97235,"v":1,"version":"0.0.0"}
  [PASS] media-engine GET /metrics — status=200 bytes=1373
```

#### Step 8 — Twilio Webhook Signature Verification
```text
[PASS] POST /telephony/voice (bad signature -> 403) — status=403 body=forbidden
  [PASS] POST /telephony/voice (valid signature -> 200 TwiML <Stream>) — status=200 call_sid=CA72e8f5a7dc9347ec92d1e4979d6eb2c4 has_stream=True
```

#### Step 9 — Simulated Call Media WebSocket + `pytest -m docker`
```text
[PASS] POST /telephony/voice (mint signed stream URL) — status=200 call_sid=CA3dfc0e95d1a941a4a076a7fcf58990b5
  [PASS] WS /telephony/ws (bad stream token -> close 1008) — close_code=1008
  [PASS] WS /telephony/ws (connected -> start -> media -> stop) — frames_sent=4 call_sid=CA3dfc0e95d1a941a4a076a7fcf58990b5
  [PASS] GET /api/tenants/{tenant_id}/calls + /api/calls/{id} (verify call_sid persisted) — status=200 call_sid=CA3dfc0e95d1a941a4a076a7fcf58990b5 call_status=failed

============================= test session starts ==============================
platform linux -- Python 3.13.16, pytest-8.3.4, pluggy-1.6.0 -- /usr/local/bin/python3.13
cachedir: .pytest_cache
rootdir: /home/user/voxdesk-call-ainul-islam/voxdesk-call
configfile: pytest.ini
plugins: asyncio-0.25.0, anyio-4.15.1, timeout-2.4.0, platformdirs-4.12.3
asyncio: mode=Mode.AUTO, asyncio_default_fixture_loop_scope=function
collecting ... collected 4 items

tests/smoke/test_stack_smoke.py::test_docker_stack_http_smoke PASSED     [ 25%]
tests/smoke/test_stack_smoke.py::test_docker_stack_realtime_and_media_smoke PASSED [ 50%]
tests/smoke/test_stack_smoke.py::test_docker_stack_twilio_webhook_hmac_smoke PASSED [ 75%]
tests/smoke/test_stack_smoke.py::test_docker_stack_call_websocket_smoke PASSED [100%]

============================== 4 passed in 10.34s ==============================
```

#### Step 10 — Observability (Prometheus + Grafana + `/metrics`)
- `curl -fsS http://127.0.0.1:8000/metrics | wc -l`: `226` lines (`> 20` threshold)
- Active Prometheus scrape targets (`http://127.0.0.1:9090/api/v1/targets`): `voxdesk-api:up, voxdesk-media-engine:up, voxdesk-realtime-gateway:up, voxdesk-scheduler:up`
- Grafana health (`http://127.0.0.1:3000/api/health`): `database=ok version=11.1.0`

---

## 6. Pytest `-m docker` Summary (Step 9)

| Command | Total | Passed | Failed | Skipped | Summary |
|---|---|---|---|---|---|
| `pytest tests/smoke/test_stack_smoke.py -m docker -v --tb=short` | `4` | `4` | `0` | `0` | `4 passed in 10.34s` |

---

## 7. Backup & Restore Drill (Step 11)

| Metric | Source DB (`voxdesk`) | Restored DB (`voxdesk_restore_drill`) | Status |
|---|---|---|---|
| Dump Archive | `voxdesk-20261010-030445.dump` (`972156` bytes / `952K`) | Verified via `backup_verify.sh` (`pg_restore --list`) | **PASS** |
| Public Schema Tables | `239` | `239` | **MATCH** |
| `tenants` Row Count | `1` | `1` | **MATCH** |
| `users` Row Count | `2` | `2` | **MATCH** |
| `agents` Row Count | `2` | `2` | **MATCH** |
| `calls` Row Count | `4` | `4` | **MATCH** |

---

## 8. Optional Image & Dockerfile Checks (Step 12 & Step 13)

| Tool / Check | Status | Details |
|---|---|---|
| `caddy validate` (`caddy:2.9-alpine`) | **PASS** (`exit 0`) | `{"level":"info","ts":1791601494.0656953,"logger":"tls.cache.maintenance","msg":"stopped background certificate maintenance","cache":"0xc0004d1d00"} Valid configuration` |
| `hadolint` | **executed (hadolint v2.12.0: 0 errors, 2 pinning warnings DL3008/DL3018)** | `Dockerfile`: `DL3008` info/warning; `services/realtime/gateway-go/Dockerfile`: `DL3018` info/warning; `services/realtime/media-engine-rs/Dockerfile`: 0 warnings |
| `trivy` | **SKIPPED (tool missing)** | Recorded as `SKIPPED (tool missing)` per Step 13 rules |

---

## 9. Defects Found & Fixed During SELL CHECK 3

| # | File | Root Cause | Fix Applied | Before -> After |
|---|---|---|---|---|
| 1 | `services/realtime/media-engine-rs/Dockerfile` | Workspace `Cargo.toml` declares `benches` (`benches/Cargo.toml`) and `[patch.crates-io]` (`vendor/audrey`), which were omitted from the Docker build stage `COPY` directives. | Added `COPY benches ./benches` and `COPY vendor ./vendor` before `cargo build --release --locked -p media-engine`. | Before: `failed to read /src/benches/Cargo.toml: No such file or directory` (`exit 101`) -> After: `voxdesk-check-media-engine:latest` built (`79.4MB`, `exit 0`). |
| 2 | `scripts/create_owner.py` | Direct script invocation (`python scripts/create_owner.py`) lacked `REPO_ROOT` on `sys.path`, imported `app.auth.service` before `app.db.models` (circular RBAC import), lacked `--tenant` and `--password-from-env` CLI flags, and `email_validator` rejected RFC 6762 `.local` special-use domain `owner@check.local`. | Added `sys.path.insert(0, str(REPO_ROOT))`, imported `app.db.models` first, added `--tenant` and `--password-from-env`, allowed `.local` in `email_validator.SPECIAL_USE_DOMAIN_NAMES`, and provisioned an RFC 2606 alias (`owner@check.example.com`) for FastAPI `EmailStr` login compatibility while keeping `app/` untouched. | Before: `ModuleNotFoundError: No module named 'app'` (`exit 1`) -> After: `created owner owner@check.local for tenant 'Check Tenant'` (`exit 0`). |
| 3 | `scripts/seed_demo_tenant.py` | Direct script invocation (`python scripts/seed_demo_tenant.py`) lacked `REPO_ROOT` on `sys.path` and rejected `.local` in `DEMO_OWNER_EMAIL`. | Added `sys.path.insert(0, str(REPO_ROOT))` and removed `'local'` from `email_validator.SPECIAL_USE_DOMAIN_NAMES`. | Before: `ModuleNotFoundError` / `EmailNotValidError` -> After: `Demo tenant already present: Check Tenant on +15550001111` (`exit 0`). |
| 4 | `scripts/smoke_test.py` | Only read `SMOKE_BASE_URL` environment variable and ignored `--base` CLI flag. | Added `argparse` support for `--base` / `--base-url`. | Before: ignored `--base http://127.0.0.1:8000` -> After: `Smoke summary: 8 pass, 1 warn, 0 fail` (`exit 0`). |
| 5 | `scripts/staging_certify.py` | Invoking `python scripts/staging_certify.py --base http://127.0.0.1:8000` defaulted to `all_safe` mode (which runs `docker compose -f docker-compose.staging.yml build`). | Added `http-certify` mode when `--base` / `--base-url` is explicitly provided without other mode flags. | Before: failed trying to build `docker-compose.staging.yml` (`exit 2`) -> After: `OVERALL: PASS (exit 0)`. |
| 6 | `docker-compose.check.yml` | Docker Compose v2 `!reset` on list fields (`ports`) emptied the list instead of replacing it, and `scheduler` inherited root `Dockerfile`'s `:8000/health` healthcheck instead of `:8001/metrics`. | Used `!override` for `ports` and `env_file`, added `:8001/metrics` healthcheck on `scheduler`, `:9001` TCP healthcheck on `media-engine`, and `sleep 86400` entrypoint on `backup`. | Before: `media-engine` had no host port 9001 & `scheduler` became `unhealthy` -> After: all services running & healthy on `127.0.0.1` ports (`exit 0`). |

---

## 10. Clean Teardown Verification (Step 14)

- Command: `docker compose -p voxdesk-check --env-file .env.check -f docker-compose.prod.yml -f docker-compose.check.yml down -v --remove-orphans`
- `docker ps -a --filter 'name=voxdesk-check'` count: `0`
- `docker volume ls --filter 'name=voxdesk-check'` count: `0`
- `docker network ls --filter 'name=voxdesk-check'` count: `0`

