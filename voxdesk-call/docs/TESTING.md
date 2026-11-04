# Testing

How to run this repository's tests so a green result actually means something.
Every command below is expected to be run from the repository root.

The suite is large (thousands of pytest nodes, two frontend test suites, a Go
SDK). Nothing here needs a network connection: the tests that would call a
provider are opt-in and skip with a printed reason when their environment is
missing.

---

## Backend: `scripts/run_full_tests.sh`

```bash
bash scripts/run_full_tests.sh              # every chunk, then a summary table
bash scripts/run_full_tests.sh --chunk truth   # one chunk
bash scripts/run_full_tests.sh --list       # chunk names and their pytest targets
bash scripts/run_full_tests.sh --no-db      # unit chunks only; DB chunks reported SKIPPED
bash scripts/run_full_tests.sh -k billing   # extra arguments are forwarded to pytest
```

What the script does, in order:

1. **Checks its prerequisites by opening a real TCP connection** to the PostgreSQL
   and Redis instances named by `DATABASE_URL` and `REDIS_URL`. It exits `2`
   before running anything when Redis is unreachable, and also when PostgreSQL is
   unreachable unless `--no-db` was passed. A run that silently degrades to
   SQLite and in-process rate limiting is not a full run, so it refuses rather
   than reporting green.
2. **Creates the database if it does not exist and runs `alembic upgrade head`.**
   `tests/jobs` and `tests/integration` assert against a migrated schema; a
   pending migration would make them fail for the wrong reason.
3. **Runs one pytest process per chunk** and writes JUnit XML to `reports/junit/`
   plus a log to `/tmp/run_full_tests_<chunk>.log`. One process per chunk keeps
   peak RSS bounded: a single process over the whole population holds every
   imported module at once and is killed on a 2 GB machine.
4. **Prints a summary table** with passed/failed/error/skipped counts per chunk and
   exits `1` if any chunk failed or timed out.

Each chunk is bounded by `TEST_CHUNK_TIMEOUT` (default 1800 s). Chunks listed in
`PER_FILE_CHUNKS` (default `truth auth`, which both import the whole FastAPI
application in nearly every module) are executed one test file at a time and
aggregated into a single chunk verdict, because they are otherwise out-of-memory
killed rather than failed. A chunk whose target is a glob (`tests/test_*.py`) always
runs file by file.

Environment variables the script reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://voxdesk@127.0.0.1:5432/voxdesk` | database to create/migrate and test against |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | rate limiter and outbox worker backend |
| `TEST_CHUNK_TIMEOUT` | `1800` | per-chunk wall clock limit, seconds |
| `JUNIT_DIR` | `reports/junit` | where per-chunk JUnit XML is written |
| `PER_FILE_CHUNKS` | `truth auth` | chunks that run one file per process (`""` disables) |
| `PYTHON` | `python3` | interpreter used for pytest and the provisioner |

Installing the Python dependencies first:

```bash
pip install -r requirements.txt -r requirements-test.txt
```

### Related commands

| Command | What it verifies |
| --- | --- |
| `python scripts/pytest_chunks.py` | the whole population chunked *by node id* with JUnit roll-up and an accounting check that no collected node disappears |
| `python scripts/reconcile_validation_results.py --ci` | collected count equals the recorded count in `reports/TEST_EVIDENCE_SUMMARY.json` |
| `python scripts/verify_docs_match_code.py` | every number and reference quoted in the docs matches this commit |
| `make verify-truth` | the same guard, as a make target |
| `make verify-hygiene` | no generated artifact is tracked by git |

`run_full_tests.sh` and `pytest_chunks.py` answer different questions: the shell
script provisions the services and gives a chunk-level verdict for a human, while
`pytest_chunks.py` proves that every collected node ran exactly once. Use the
first for day-to-day runs and the second before a release.

### Services on a container without them

`tests/jobs/test_recovery.py` asserts a `postgresql+asyncpg://` `DATABASE_URL`, and
the identity rate limiter and outbox worker use Redis when `REDIS_URL` is set. If
neither binary is installed, any PostgreSQL 16 server and any Redis server work;
export both URLs, then run the script. Do not "fix" a failure there by pointing
`DATABASE_URL` at SQLite — the test exists to catch exactly that.

---

## Frontend: `dashboard` (Vite, the one that is served)

```bash
cd dashboard
npm ci
npx tsc --noEmit        # types
npx vitest run          # unit and component tests
npx vite build          # production bundle
npm run routes:registry # regenerate src/__tests__/fixtures/route-registry.json
npm run routes:registry:check  # fail when that fixture is stale (use in CI)
```

`src/__tests__/fixtures/route-registry.json` is a deliberately frozen copy of the
route table. `src/__tests__/no-generated-tails.test.ts` asserts that the real
`ROUTES` array matches it, so adding a route is an explicit edit: run
`npm run routes:registry` and review the diff. The generator loads the router
through Vite's SSR loader, i.e. the same compiler the build uses.

## Frontend: `dashboard-next` (Next.js shadow console)

```bash
cd dashboard-next
npm ci
npx tsc --noEmit
npx vitest run
npx next build          # Turbopack
npm run build           # webpack
```

The browser voice client is the `sdk/web` package, consumed as
`"@voxdesk/web-sdk": "file:../sdk/web"`. Next refuses to compile a module whose
real path escapes the workspace root it inferred, so `next.config.mjs` declares
`turbopack.root` and `outputFileTracingRoot` as the repository root and lists the
package in `transpilePackages` (it ships TypeScript sources). A relative import
that climbs out of `dashboard-next/` does not build under Turbopack.

## Go SDK

```bash
cd sdk/go
go vet ./...
go test ./...
gofmt -l .          # must print nothing
```

`sdk/go/README.md` documents the client surface, retry and pagination semantics.

---

## Reading a failure

- `TIMEOUT` (exit 124): the chunk exceeded `TEST_CHUNK_TIMEOUT`. Raise it for a slow
  machine; do not lower it to make the summary green.
- chunk killed with exit 137: out of memory, not an assertion. Add the chunk to
  `PER_FILE_CHUNKS` or run it with `--chunk <name>` alone.
- `MISSING`: the chunk's directory or glob does not exist, i.e. the chunk list is
  stale.
- `SKIPPED`: a database chunk that `--no-db` excluded. The summary counts it and
  the exit code stays `0`, so read the table, not just the exit status.
- a skip total that looks too low: check it against the JUnit XML. The summary is
  parsed from pytest's last output line, and a file whose entire output is
  `16 skipped` was once counted as zero. The script now matches every outcome word,
  but the XML under `reports/junit/` is the authority when the two disagree. Note
  that this directory is not emptied between runs, so summing it after several runs
  double-counts — the script prints a warning naming any JUnit file its own run did
  not write.
- `EMPTY`: the chunk collected no tests. A chunk that runs a glob (`tests/test_*.py`)
  can legitimately touch a module that holds no tests, so pytest's exit code `5`
  ("no tests collected") is not a failure here — the chunk is flagged as `EMPTY`
  so a stale path is still visible in the summary.
