# VoxDesk Helm Chart

Deploys the full VoxDesk platform — HTTP API, scheduler, Next.js frontend,
Go realtime gateway, Rust media engine, plus the migration hook, backup CronJob,
NetworkPolicies and Prometheus monitoring — into a Kubernetes cluster.

The chart deliberately consumes an existing Kubernetes Secret. It never puts
API keys, OAuth tokens, database passwords, JWT keys, or provider credentials
in `values.yaml` or generated manifests. `helm template` on any profile emits
**zero** `kind: Secret` objects; a test enforces that
(`tests/deployment/test_helm_chart_completeness.py`).

```sh
helm lint infra/helm/voxdesk
helm template infra/helm/voxdesk -f infra/helm/voxdesk/values-prod.yaml \
  | kubeconform -strict -summary
bash scripts/check_helm.sh        # the gate: lint + render + kubeconform + content + alert sync
```

Rendered object counts: **base 21**, **prod 47**, **staging 45**, all with
`Skipped: 0` under `kubeconform -strict`.

---

## The five workloads

| Workload | Kind | Image (default) | Port | Probes |
|---|---|---|---|---|
| `api` | Deployment | `image.repository:tag` | 8000 | `/health` startup + liveness, `/health/ready` readiness |
| `scheduler` | Deployment | `image.repository:tag` | `scheduler.metricsPort` (8001) | `/metrics` startup, readiness, liveness |
| `frontend` | Deployment | `ghcr.io/voxdesk/voxdesk-call` | 3000 | `/` startup, readiness, liveness |
| `realtime-gateway` | Deployment | `ghcr.io/voxdesk/voxdesk-realtime-gateway` | 8790 | `/healthz` startup + liveness, `/readyz` readiness |
| `media-engine` | Deployment | `ghcr.io/voxdesk/voxdesk-media-engine` | 9001 TCP, 5000 UDP | `/v1/health` startup, readiness, liveness |

Every container in every workload carries the same `containerSecurityContext`:
`allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`,
`capabilities.drop: [ALL]`, `runAsUser/runAsGroup: 10001`. Two workloads are
exceptions by necessity and say so inline:

- **media-engine** runs `hostNetwork: true` because RTP is UDP and NAT'd pod
  networking breaks SDP. It therefore uses `dnsPolicy: ClusterFirstWithHostNet`,
  publishes `mediaEngine.publicIp`, and is pinned by
  `nodeSelector: voxdesk.io/role: media` plus a toleration. It rolls with
  `maxSurge: 0` / `maxUnavailable: 1` and `replicaCount: 1`, because two pods
  cannot own the same UDP port.
- **backup** mounts a real PersistentVolumeClaim; it writes dumps there.

### Fail-closed guards (deliberate, tested)

These `fail` the render rather than produce a broken deployment:

| Condition | Why |
|---|---|
| `mediaEngine.hostNetwork: false` with empty `mediaEngine.publicIp` | without a public IP the SDP advertises a pod address no carrier can reach |
| `backup.persistentVolume.create: false` with empty `existingClaim` | the CronJob would write dumps into the container filesystem and lose them |
| `externalSecrets.secretStoreRef.name` empty while `externalSecrets.enabled: true` | the ExternalSecret would render unusable |

---

## Frontend / API ingress split

The frontend and API are separate Services with separate ingresses, because
they scale on different signals and the API must stay reachable while the
frontend is being replaced.

- `frontend.ingress.*` — `className`, `host`, `paths` (default `[/]`),
  `tls`. Serves the Next.js standalone server on `frontend.service.port: 3000`.
- The API ingress is rendered from the top-level `ingress.*` values and routes
  the API prefixes (`/api`, `/auth`, `/channels`, `/health`, `/messaging`,
  `/public`, `/scim`, `/telephony`, `/webhooks`, `/widget`, `/ws`,
  `/.well-known`, `/metrics`, `/openapi.json`, `/redoc`, `robots.txt`,
  `/sitemap.xml`, `/security.txt`) to `api:8000`.

In production the compose/Caddy profile does the same split at the edge:
`/realtime/ws` → realtime gateway, the API prefixes above → `api:8000`,
everything else → `web:3000`. See `Caddyfile`.

> If you front the cluster with a single ingress instead, keep `/api` pointing
> at the API Service. Routing `/api` to the frontend will serve Next.js 404s for
> every API call — this is the failure mode the split exists to prevent.

## Realtime gateway and media engine

The two are a pair: the gateway terminates WebSocket/WebRTC signalling and
speaks to the media engine over `mediaEngineTimeoutSeconds` (1.5 s default).

- `realtimeGateway.broker` is `memory` in the base profile and **`redis` in
  `values-prod.yaml`**. `memory` cannot work across two gateway pods, so leaving
  it on `memory` with `replicaCount > 1` silently splits sessions across pods.
- `realtimeGateway.engineSteer: off` in base; `values-prod.yaml` turns it on so
  the gateway steers engines instead of assuming a single one.
- Secrets are read via `secretKeyRef` from the `voxdesk-runtime` Secret:
  `jwtSecretKey: JWT_SECRET`, `ingestSecretKey: REALTIME_GATEWAY_INGEST_SECRET`,
  `metricsTokenKey: METRICS_TOKEN`. The values hold **key names**, never values.
- HPA scales the gateway on CPU and (optionally) connections, with a 600 s
  scale-down stabilization window and at most 1 pod removed per 120 s — voice
  sessions are long-lived, and a fast scale-in kills calls.

## Scheduler

`app/scheduler` is an in-process APScheduler worker. It exposes Prometheus
metrics on `scheduler.metricsPort` (default **8001**, matching
`SCHEDULER_METRICS_PORT` in `app/core/config.py`), which is what all three of
its probes hit. It was the one workload shipping without probes or a container
security context before PART 17B, so the chart now guarantees both; a test
asserts every Deployment has 3 probes and a hardened context.

## Migration hook

`migrateJob` renders a `pre-install,pre-upgrade` Helm hook Job with
`backoffLimit: 1`, `activeDeadlineSeconds: 900` and
`ttlSecondsAfterFinished: 3600`. It runs `alembic upgrade head` before the new
API pods roll, so a failed migration aborts the release instead of leaving a
half-migrated deployment serving traffic. The API image entrypoint does **not**
migrate; the hook is the only migrator.

## Backup

`backup-cronjob` runs `pg_dump` on `backup.schedule` (default `0 2 * * *`,
`Etc/UTC`), retains `backup.retain: 14` dumps on a PVC, and optionally pushes to
`backup.remote`. It reads credentials from `backup.secretName` using the keys
`backup.userKey` / `databaseKey` / `passwordKey` (defaults `POSTGRES_USER` /
`POSTGRES_DB` / `POSTGRES_PASSWORD`). See `docs/RUNBOOKS/backup-restore.md`.

## NetworkPolicy — scope and a deliberate gap

`networkPolicy.enabled: true` applies a **default-deny ingress** NetworkPolicy
to every pod the release owns, then re-admits:

- the ingress controller namespace/pods (`networkPolicy.ingressController.*`),
- database traffic on `networkPolicy.database.port` (5432),
- Redis on `networkPolicy.redis.port` (6379),
- Prometheus in `networkPolicy.monitoring.*`, so scrapes keep working.

**Egress is deliberately unrestricted**, and the policy file says so in its
header. Voice traffic needs provider endpoints whose CIDRs are not knowable at
chart-render time (carrier SIP, TTS/STT, LLM providers, webhook receivers), and
a wrong egress allowlist takes down calling rather than tightening it. Egress
filtering belongs in the CNI/cloud layer where the destination set is known.
Do not read the absence of egress rules as an oversight.

Selectors render bare under `podSelector:` and label maps under `matchLabels:`;
the values are shaped that way on purpose, because double-wrapping them yields a
policy that parses and matches nothing.

## Monitoring

`serviceMonitor.enabled: true` renders a Prometheus Operator `ServiceMonitor`
plus a `PrometheusRule` (default off: `serviceMonitor.prometheusRule.enabled`).

- Scrape interval `serviceMonitor.interval: 15s`, timeout `10s`.
- Rule group `voxdesk` — the 11 platform alerts, kept byte-identical to
  `observability/alerts.yml`. `scripts/sync_chart_alerts.sh --check` fails if the
  two drift; edit the source file, then run `scripts/sync_chart_alerts.sh`.
- Rule group `voxdesk-platform` — `VoxDeskBackupJobFailed`,
  `VoxDeskMigrationJobFailed`, `VoxDeskPodCrashLooping`,
  `VoxDeskRealtimeGatewaySaturated`. Disable with
  `serviceMonitor.prometheusRule.includePlatformRules: false` if you run these
  in a central rule set.

Alert `runbook_url`s point at `docs/RUNBOOKS/*`.

## ExternalSecret (opt-in)

`externalSecrets.enabled: false` by default — most operators create
`voxdesk-runtime` themselves. When enabled the chart renders an `ExternalSecret`
that syncs `externalSecrets.data` entries (default `JWT_SECRET` from
`voxdesk/jwt`) from `externalSecrets.secretStoreRef` every
`externalSecrets.refreshInterval` (1 h). It renders **only** an ExternalSecret;
the resulting Secret is created by the External Secrets Operator, never by Helm.

## Usage

```sh
kubectl create secret generic voxdesk-runtime \
  --from-env-file=/secure/operator-only/voxdesk.env

helm upgrade --install voxdesk ./infra/helm/voxdesk \
  -f infra/helm/voxdesk/values-prod.yaml \
  --set image.repository=registry.example.com/team/voxdesk-call \
  --set image.tag=1126370
```

PostgreSQL and Redis are expected to be managed dependencies in production.
`values-prod.yaml` enables `networkPolicy`, `serviceMonitor` and
`serviceMonitor.prometheusRule`, turns on the backup CronJob, and switches the
gateway broker to Redis.

## Verification

| Check | Command |
|---|---|
| Chart gate | `bash scripts/check_helm.sh` |
| Chart tests | `pytest tests/deployment/test_helm_chart_completeness.py -q` |
| Alert sync | `bash scripts/sync_chart_alerts.sh --check` |
| Cluster smoke | `.github/workflows/helm-e2e.yml` (kind v1.31.0, install → `/health` → uninstall) |

`scripts/check_helm.sh` is the authority: it lints, renders all three profiles,
validates with `kubeconform -strict`, and asserts the content properties above
(5 Deployments, a backup CronJob with a PVC, the pre-upgrade migrate hook,
resources on every container, and no rendered Secret).
