# Runbook — API killed by the OOM reaper

## Symptom

Any of these, usually together:

- Every console page starts failing at once, in ~150 ms each, with
  `SyntaxError: Unexpected token 'u', "upstream 1"...` — that is the dev proxy
  returning its own `upstream 1` error page because the API socket is gone.
- `/health` returns `000` (connection refused) instead of `200`.
- `dmesg` shows `Out of memory: Killed process <pid> (python3)` with
  `anon-rss:6xxxxxkB`.

The trap: this presents as a **mass accessibility failure** in the Playwright
run. It is not. The gate is measuring an error page.

## Check first (10 seconds)

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
sudo dmesg | grep -i "killed process" | tail -3
free -m | sed -n '2,3p'
```

If `/health` is `000`, the API is dead. Do not look at the tests.

## Why it happens

uvicorn with migrations applied and Pipecat imported **peaks around
700–730 MB** while browser suites are driving it, and releases slowly enough
that the peak overlaps Chromium's. Add ~700 MB of Chromium on a 2 GB box and
the kernel picks the largest process — the API — and kills it.

Measured on this codebase: `anon-rss:673808kB` per kill, three in one session.
The same process settled at **102 MB** once the suites stopped, with no
restart — so the problem is the peak, not a leak. Size for the peak.

`MALLOC_ARENA_MAX=2` and the trim/mmap thresholds were tried and changed
nothing measurable. Do not spend time on them; shard the suite and add swap
(for a dev box) or memory (for production).

## Fix

**Immediate (dev box):**

```sh
# 1. Give the kernel somewhere to go instead of killing.
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
free -m | tail -1        # Swap: 2047 ... confirms it is live

# 2. Restart the API.
cd <repo> && DATABASE_URL=... python3 -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --log-level error --no-access-log
```

Verify **before** re-running any suite:

```sh
sleep 12 && curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health   # must be 200
```

The first request after boot pays the import cost; waiting ~10 s avoids a false
`000`.

**Structural (browser suites):** slice the console audit.

```sh
A11Y_SHARD=1/2 npx playwright test e2e/a11y.spec.ts -g "operator console"
A11Y_SHARD=2/2 npx playwright test e2e/a11y.spec.ts -g "operator console"
```

28 pages per process instead of 56. Each process gets its own Chromium, and the
API never holds both.

**Production:** size the pod from the 700 MB figure plus headroom, set a memory
`limit`, and alert on `container_memory_working_set_bytes` at 80% — not on
restarts, which is too late.

## Write down

- The RSS at kill time and the total memory of the box.
- How many pages the failing run had completed (that tells you whether sharding
  is enough or the box is simply too small).
- Whether the gate had already reported real violations earlier in the same run
  — those are genuine and should not be lost when the run is re-done.
