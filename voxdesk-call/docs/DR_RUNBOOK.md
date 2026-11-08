# VoxDesk — PostgreSQL Disaster Recovery (DR) Runbook

This runbook documents the automated backup, verification, and restore procedure
for the VoxDesk PostgreSQL database, backed by:
- `scripts/backup.sh` — `pg_dump` custom-format backup with SHA-256 checksum
- `scripts/backup_verify.sh` — `pg_restore --list` structural and checksum verification
- `scripts/restore.sh` — clean database restore + Alembic head & row-count verification
- `scripts/dr_drill.sh` — end-to-end automated DR drill writing `evidence/dr/dr_drill_report.json`

---

## 1. Recovery Objectives & Measured Drill Evidence

From the recorded drill report in `evidence/dr/dr_drill_report.json`
(executed at `2026-10-08T17:03:22Z` across `239` public tables):

| Metric | Target SLO | Measured Drill Value | Source |
|---|---|---|---|
| **RPO (Recovery Point Objective)** | `<= 60 s` (WAL archiving) / `0` rows on snapshot | `0.3491 s` (`0` data-loss rows) | `evidence/dr/dr_drill_report.json` |
| **RTO (Recovery Time Objective)** | `<= 15 min` | `2.2289 s` (schema + seed dataset) | `evidence/dr/dr_drill_report.json` |
| **Backup Duration** | `<= 5 min` | `0.2946 s` (`1,955,302` bytes) | `evidence/dr/dr_drill_report.json` |
| **Backup Verification Duration** | `<= 60 s` | `0.0392 s` | `evidence/dr/dr_drill_report.json` |

> **Scope Note**: The sub-3-second RTO above reflects an automated drill on a
> baseline schema and seed dataset (`tenants`, `users`, `agents`, `calls`,
> `turns`, `call_latency_stats`). Production RTO scales linearly with total
> database size, object storage throughput, and network bandwidth.

---

## 2. Running an Automated DR Drill (`scripts/dr_drill.sh`)

Run the non-destructive drill against isolated temporary databases
(`voxdesk_dr_source` -> `voxdesk_dr_target`):

```bash
bash scripts/dr_drill.sh --output evidence/dr/dr_drill_report.json
cat evidence/dr/dr_drill_report.json
```

What `scripts/dr_drill.sh` performs:
1. Provisions isolated `voxdesk_dr_source` and `voxdesk_dr_target` PostgreSQL databases.
2. Applies `alembic upgrade head` on `voxdesk_dr_source` and seeds canonical multi-tenant call/turn/latency records.
3. Captures a custom-format dump via `scripts/backup.sh` and verifies SHA-256 + `pg_restore --list` via `scripts/backup_verify.sh`.
4. Restores into `voxdesk_dr_target` via `scripts/restore.sh` and asserts exact Alembic revision equality and zero row loss across `tenants`, `users`, `agents`, `calls`, `turns`, and `call_latency_stats`.

---

## 3. Production Emergency Restore Procedure

1. **Enter Graceful Drain / Maintenance Mode**:
   Scale API and scheduler deployments down or trigger drain so no new writes land on the degraded primary:
   ```bash
   kubectl -n voxdesk scale deploy/voxdesk-api deploy/voxdesk-scheduler --replicas=0
   ```
2. **Verify Backup Integrity Before Restore**:
   ```bash
   bash scripts/backup_verify.sh /path/to/voxdesk-YYYYMMDD-HHMMSS.dump
   ```
3. **Restore to Target PostgreSQL Instance**:
   ```bash
   DATABASE_URL="postgresql://voxdesk:<password>@<new-db-host>:5432/voxdesk" \
     bash scripts/restore.sh /path/to/voxdesk-YYYYMMDD-HHMMSS.dump
   ```
4. **Verify Migration Head & Audit Chain Integrity**:
   ```bash
   alembic current
   alembic heads
   ```
   Confirm `alembic current` matches the single repository head (`0069_voice_demo_sessions`).
5. **Resume Traffic**:
   Update `DATABASE_URL` in the secret store if failing over to a new host, restore replica counts, and verify `/health/ready` returns `200 OK`.

Related operational runbooks:
- `docs/OPS.md` — routine backups, secret rotation, and webhook DLQ redrive
- `docs/STEP10-BACKUP-SYNC.md` — off-host backup synchronization
- `docs/runbooks/` — incident response playbooks (`db-failover.md`, `high-latency.md`, `provider-outage.md`, `webhook-backlog.md`)
