# Docker build proof

The old `DOCKER_BUILD_SIMULATION.md` described a build nobody had run: the
sandbox had no Docker daemon, so the document narrated what a build *would* do
and finished with a tick. It is now a pointer, and this document describes the
proof that replaced it. Nothing here is asserted without a file that the script
wrote.

## What produces the proof

```sh
make staging-proof
# = python scripts/staging_certify.py --proof \
#       --evidence-dir evidence/staging/<UTC-date> --with-drills
```

The command brings up `docker-compose.check.yml` under the project name
`voxdesk-check`, with `init: true` and a 30-second stop grace so PID 1 reaps
children and a shutdown is a drain rather than a kill. Every step runs through
`run_logged`: the stdout is captured to `evidence/staging/<date>/raw/<step>.log`,
and the step records its exit code, wall-clock duration and the SHA-256 of that
log. Teardown runs even when a step fails, so a broken proof does not leave a
stack behind.

## Steps, in order

| # | Step | Command | Pass condition |
|---|---|---|---|
| 01 | `compose up --build` | `docker compose -p voxdesk-check -f docker-compose.check.yml up -d --build` | exit 0 |
| 02 | `alembic upgrade head` | `docker compose exec api alembic upgrade head` | exit 0 |
| 03 | owner creation | `scripts/create_owner.py` | exit 0, or non-zero only because the owner already exists — the HTTP smoke then logs in, which is the check that matters |
| 04 | HTTP smoke | `scripts/stack_smoke.py http --json` | exit 0 |
| 05 | signed webhook smoke | `scripts/stack_smoke.py twilio --json` | exit 0; a valid signature returns TwiML containing `<Stream>`, a tampered one returns 403 |
| 06 | WebSocket media handshake | `scripts/stack_smoke.py call-ws --json` | exit 0 |
| 07 | web-call token flow | `scripts/stack_smoke.py web-call-token --json` | exit 0 |
| 08 | database verify | `psql -c "select current_database(), (select count(*) from information_schema.tables where table_schema='public');"` | exit 0 |
| 09 | backup → restore drill | `backup_restore_drill()` | a dump is taken, restored into a scratch database, and row counts compared |
| 10 | graceful drain | `graceful_drain_check()` | in-flight requests complete inside `DRAIN_GRACE_SECONDS` |
| 99 | `compose down` | `docker compose down` | exit 0 (always attempted) |

## Output

```
evidence/staging/<UTC-date>/
├── staging_proof.json          # schema voxdesk.staging_proof/v1
├── staging_proof.log
└── raw/
    ├── 01-compose-up.log
    ├── 02-alembic-upgrade.log
    ├── 03-create-owner.log
    ├── 04-http-smoke.log
    ├── 05-twilio-webhook-smoke.log
    ├── 06-call-ws-smoke.log
    ├── 07-web-call-token-smoke.log
    ├── 08-db-verify.log
    ├── 09-backup-drill.log
    ├── 10-drain.log
    └── 99-compose-down.log
```

`staging_proof.json` carries the schema, the UTC timestamp, the commit sha, the
environment, and one record per step: `{step, status, detail, exit_code,
duration_seconds, log, log_sha256}`. `overall` is `PASS` only when every step
is `PASS`; anything else is `FAIL` or `BLOCKED` and the command exits non-zero.

`BLOCKED` is not a failure. It is what the script reports when Docker is absent
(`docker: command not found`), and it names the missing binary rather than
printing a tick. A proof that cannot run says so.

## Continuous re-proof

`.github/workflows/staging-proof.yml` runs the same command on a weekly cron and
on `workflow_dispatch`, on a runner where Docker exists, and uploads
`evidence/staging/<date>/` as an artifact. The committed copy in this repository
is whatever the last run produced — check `generated_at` in
`staging_proof.json` before quoting it.
