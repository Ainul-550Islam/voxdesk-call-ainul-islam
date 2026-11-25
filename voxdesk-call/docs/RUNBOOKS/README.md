# Runbooks

One page per failure mode. Each names the symptom first, because that is what
the person paged at 03:00 has. Every runbook ends with what to write down: an
incident that is fixed but not recorded is an incident that will happen again.

| Runbook | Reach for it when |
| --- | --- |
| [api-oom.md](api-oom.md) | API restarting, 502s from the front door, `Killed process … python3` in `dmesg` |
| [front-door-502.md](front-door-502.md) | The site answers 502/504 but the API is healthy |
| [standalone-build-lock.md](standalone-build-lock.md) | `A previous build that didn't exit cleanly`, or the standalone server 500s after a rebuild |
| [restore-drill.md](restore-drill.md) | Fast round-trip drill (automated test) |
| [backup-restore.md](backup-restore.md) | The measured operator drill, and restoring onto a running deployment |
| [db-failover.md](db-failover.md) | `VoxDeskDatabaseDown`, `/health/ready` 503 with `checks.database.ok == false` |
| [dr-failover.md](dr-failover.md) | The database *or the region* is gone: RTO/RPO, promotion, DNS cut-over |
| [provider-outage.md](provider-outage.md) | `VoxDeskProviderErrorBurst`, `VoxDeskCallFailureRate`, circuit-breaker failover |
| [high-latency.md](high-latency.md) | `VoiceLatencyP95*`, `VoxDeskSlowP95` |
| [webhook-backlog.md](webhook-backlog.md) | `VoxDeskStuckSideEffects`, `VoxDeskSchedulerDown`, `VoxDeskJobStale` |
| [certificate-rotation.md](certificate-rotation.md) | TLS expiry, or rotating JWT / envelope / webhook signing keys |
| [accessibility-gate-red.md](accessibility-gate-red.md) | The Playwright accessibility gate fails in CI |
| [REAL-E2E-RUNBOOK.md](REAL-E2E-RUNBOOK.md) | Running the operator-driven, real-provider end-to-end call |

Every alert's `runbook` annotation points into this directory, spelled
`RUNBOOKS` in capitals, because these files live on a case-sensitive filesystem.
The directory used to be spelled with a lower-case `runbooks`; the annotations
and the SLO table always said `RUNBOOKS`, so on the Linux host where the alert
fires every one of those links was dead while `ls` on a laptop showed the file
present. PART 17B renamed the directory and `tests/deployment/test_runbook_links.py`
now fails if a second spelling reappears.

**The rule that covers all of them:** find out whether the product is broken or
the harness is broken *before* changing code. In P17B-04, nine reported
accessibility failures were one dead API, three `networkidle` timeouts, one page
that does not exist, and two genuine bugs. Changing the first thing you see will
usually be changing the wrong thing.
