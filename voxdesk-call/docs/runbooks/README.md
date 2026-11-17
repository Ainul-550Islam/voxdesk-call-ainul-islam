# Runbooks

One page per failure mode. Each names the symptom first, because that is what
the person paged at 03:00 has. Every runbook ends with what to write down: an
incident that is fixed but not recorded is an incident that will happen again.

| Runbook | Reach for it when |
| --- | --- |
| [api-oom.md](api-oom.md) | API restarting, 502s from the front door, `Killed process … python3` in `dmesg` |
| [front-door-502.md](front-door-502.md) | The site answers 502/504 but the API is healthy |
| [standalone-build-lock.md](standalone-build-lock.md) | `A previous build that didn't exit cleanly`, or the standalone server 500s after a rebuild |
| [restore-drill.md](restore-drill.md) | Quarterly drill, or after any data-loss event |
| [accessibility-gate-red.md](accessibility-gate-red.md) | The Playwright accessibility gate fails in CI |

**The rule that covers all of them:** find out whether the product is broken or
the harness is broken *before* changing code. In P17B-04, nine reported
accessibility failures were one dead API, three `networkidle` timeouts, one page
that does not exist, and two genuine bugs. Changing the first thing you see will
usually be changing the wrong thing.
