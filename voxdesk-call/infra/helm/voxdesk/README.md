# VoxDesk Helm chart

The chart deliberately consumes an existing Kubernetes Secret. It never puts
API keys, OAuth tokens, database passwords, JWT keys, or provider credentials
in `values.yaml` or generated manifests.

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
