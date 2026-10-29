# Buyer deployment guide

Every command in this document is one that exists in the repository. Where the
guide says a thing is validated, it names the script or test that validates it.

## 1. What you are deploying

`docker-compose.prod.yml` runs eleven services:

| Service | Role | Notes |
|---|---|---|
| `db` | PostgreSQL 16 (`postgres:16-alpine`) | publishes **no** host port; reachable only on the compose network |
| `redis` | Redis 7 (`redis:7-alpine`) | publishes **no** host port |
| `api` | FastAPI application | bound to `127.0.0.1:8000`; `init: true`, `stop_grace_period: 30s`; waits for `db` health |
| `realtime-gateway` | Go realtime gateway | WebSocket media |
| `media-engine` | Rust media engine | audio pipeline |
| `scheduler` | background jobs | campaigns, retention |
| `backup` | database dumps | feeds the restore drill |
| `prometheus` | metrics | |
| `grafana` | dashboards | |
| `caddy` | TLS termination, HSTS, security headers | the only service publishing 80/443 |

Two images are pinned by digest-level tags rather than `latest`; the static
deployment tests fail the build if a `latest` tag appears in a compose file or
Helm values file (`tests/deployment/test_deploy_assets_static.py`).

## 2. Single host

```sh
cp .env.example .env
$EDITOR .env                       # see §3 -- the app refuses to boot on weak secrets
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
docker compose -f docker-compose.prod.yml exec api python -m scripts.create_owner \
    --email you@example.com --password '…' --tenant 'Acme'
docker compose -f docker-compose.prod.yml ps
```

Confirm before going further:

```sh
bash scripts/verify_deploy_assets.sh          # helm, terraform, caddy, compose -- real validation
python scripts/stack_smoke.py http \
    --base https://your-domain --env-file .env --json
python scripts/stack_smoke.py twilio \
    --base https://your-domain --env-file .env --json   # valid signature -> TwiML <Stream>, tampered -> 403
```

## 3. Configuration the application refuses to start without

These are not recommendations; `tests/security/test_boot_secret_policy.py`
asserts each one, and the app raises at startup rather than degrading:

| Setting | Requirement |
|---|---|
| `SECRET_KEY` / `JWT_SECRET` | not the shipped default, not empty, and long enough to be a key rather than a placeholder |
| `TELEPHONY_WEBHOOK_SECRET` | required in production — unsigned carrier webhooks are rejected |
| `PUBLIC_BASE_URL` | must be `https://` in production (this is what turns on HSTS and secure cookies) |
| `TWILIO_SKIP_WEBHOOK_VERIFY` | refused in production |
| `E2E_ENABLED` | refused in production |
| `CORS_ORIGINS` | an explicit list; `*` is rejected, and credentials are never returned for an unknown origin |

Copy `.env.example` and fill it in; the full key list with comments is in that
file.

## 4. Kubernetes

```sh
helm lint infra/helm/voxdesk -f infra/helm/voxdesk/values-prod.yaml
helm template voxdesk infra/helm/voxdesk \
    -f infra/helm/voxdesk/values-prod.yaml | kubeconform -strict -
helm upgrade --install voxdesk infra/helm/voxdesk \
    -f infra/helm/voxdesk/values-prod.yaml
```

`values-prod.yaml`: 3 replicas, HPA 3–20, PodDisruptionBudget `minAvailable: 2`.
`values-staging.yaml`: 1 replica, HPA 1–4, PDB `minAvailable: 1`. Both set
requests and limits, run as non-root with a read-only root filesystem and all
capabilities dropped, and define startup, readiness and liveness probes.
`terminationGracePeriodSeconds` exceeds the drain timeout plus the metrics flush,
so a rolling update does not truncate in-flight calls.

The chart ships a `coturn` subchart for WebRTC traversal.

## 5. Cloud infrastructure

`infra/terraform` has `envs/{dev,staging,prod}` and modules
`alb, assets, eks, failover, network, observability, platform_v2_assets, rds,
redis`.

```sh
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform/envs/prod init -backend=false
terraform -chdir=infra/terraform/envs/prod validate
tflint --chdir=infra/terraform
```

All four of those, plus the Helm checks and `caddy validate`, are run by
`bash scripts/verify_deploy_assets.sh` and in CI by
`.github/workflows/deploy-assets.yml`. A missing tool is reported as a failure
with an install hint, not skipped.

## 6. TLS and edge

`Caddyfile` terminates TLS and sets HSTS, `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY` and `Referrer-Policy`. `infra/caddy/edge-global.caddy`
is the multi-region edge variant; on-demand TLS is gated by an `ask` endpoint so
the server cannot be used as an open certificate issuer.

## 7. Backups and recovery

The `backup` service takes scheduled dumps. To prove a dump can be restored:

```sh
make staging-proof      # includes the backup -> restore drill and row-count comparison
```

## 8. Verifying a handover

```sh
make verify-truth            # every number in the docs equals the code
make verify-hygiene          # no generated artifact is tracked by git
make security-evidence       # reports/SECURITY_EVIDENCE_SUMMARY.json
make polyglot-evidence       # Go, Rust and C++ services: build, test, lint
bash scripts/verify_deploy_assets.sh
python scripts/pytest_chunks.py            # the full test population, chunked
python scripts/reconcile_validation_results.py --ci
```

## 9. What this guide does not cover

Live-provider credentials. Carriers, KMS and speech providers are configured per
tenant in the application; proving they work is
[docs/LIVE_EVIDENCE.md](LIVE_EVIDENCE.md), and it is opt-in and cost-guarded.
