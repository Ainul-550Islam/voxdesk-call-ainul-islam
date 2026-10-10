# VoxDesk Helm Chart

The chart deliberately consumes an existing Kubernetes Secret. It never puts
API keys, OAuth tokens, database passwords, JWT keys, or provider credentials
in `values.yaml` or generated manifests.

## Features (Part 8 / Gate G9)

- **HorizontalPodAutoscaler (`autoscaling/v2`)**: Scales API pods on the live
  `voxdesk_active_calls` pod metric (`targetConcurrentCallsPerPod: "25"`,
  aligned with the single-worker capacity knee point in `docs/CAPACITY_MODEL.md`)
  and CPU utilization (`70%`), with a 300-second scale-down stabilization window.
- **PodDisruptionBudget (`policy/v1`)**: Guarantees `minAvailable: 1` during
  voluntary node drains and cluster upgrades for both API and scheduler deployments.
- **Graceful Drain Alignment (`terminationGracePeriodSeconds: 60`)**: Pairs a
  5-second `preStop` sleep hook with `SHUTDOWN_DRAIN_TIMEOUT_SECONDS=45` and
  `SHUTDOWN_FLUSH_TIMEOUT_SECONDS=10` (`app/core/graceful_shutdown.py`) so active
  voice calls complete and outbox events flush before SIGKILL.
- **Readiness Gates & Probes**: Configures pod `readinessGates`
  (`voxdesk.io/db-ready`, `voxdesk.io/redis-ready`, `voxdesk.io/providers-ready`)
  alongside `/health/ready` (`app/core/health.py`), which verifies PostgreSQL
  (`SELECT 1`), Redis (`PING`), required voice provider credentials, and
  non-draining node status.

## Usage

```sh
kubectl create secret generic voxdesk-runtime \
  --from-env-file=/secure/operator-only/voxdesk.env
helm upgrade --install voxdesk ./infra/helm/voxdesk \
  --set image.repository=registry.example.com/team/voxdesk-call \
  --set image.tag=1126370 \
  --set-string config.databaseUrl='postgresql+asyncpg://...' \
  --set-string config.redisUrl='redis://redis:6379/0'
```

The API image's entrypoint runs Alembic migrations before serving. PostgreSQL
and Redis are expected to be managed dependencies in production.
