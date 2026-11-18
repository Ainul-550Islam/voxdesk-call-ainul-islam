# PART 10 — Snapshot Truth Reset (Gate G10)

Branch `fix/part10-truth-reset`, from `main` @ `b95c5be`. Written 2026-11-18 (UTC).
Every number in this report is a command output, not a recollection; the command is
printed next to each result so it can be re-run.

What G10 claims: **the tree now says what the reports say** — the generated dumps,
nested archives, compiled objects, the retired Vite console and the unmounted route
module are gone; the truth guards are green on a fresh export that has no `.git`; and
no buyer-facing document quotes a number or an evidence status that a script cannot
re-derive. What it does **not** claim is stated in §9.

---

## 1. P10-00 — root cause, measured

`reports/PART_10_REPORT.md` in the snapshot that was handed over declared every PART 10
deletion done. The tree disagreed: `git ls-files` at `b95c5be` returned **5483** files,
`python3 scripts/check_repo_size.py` reported a **100,624,574-byte** tree with **27**
findings, and `python3 -m pytest tests/truth -q` failed **23** tests. The mismatch is
the defect the round-2 spec describes in §1.1.2 ("tree ≠ report"): PART 10's guards,
tests and report were committed; its deletions were not.

Same conclusion for the other headline defects in the spec: they describe a *different*
export than the one in git. Verified against this tree, not assumed:

| Spec defect | Measured state in `b95c5be` |
|---|---|
| 94 NUL-corrupted `app/api/*.py` clones (P10-01) | 0. Whole-tree scan: 35 NUL-bearing files, all binaries (`.wav`, `.a`, `.png`, `.ico`); none under `app/api`. `scripts/verify_no_null_bytes.py` → `[]` |
| `app/api/advanced_analytics_routes.py` (P10-03) | absent |
| `realtime-engine/`, `voxdesk-concurrency-engine/`, `voxdesk-native-audio-engine/` (P10-02) | all three absent; `services/realtime/` kept as required |
| 15 filler HTML pages in `dashboard/src/pages/` (P10-04) | no `.html` in that tree; the whole console was removed here (§3), so the claim is moot in the shipped tree |
| ten stale long-name migrations (P10-01b) | already resolved; `scripts/verify_alembic_graph.py` → `[]` |
| `reports/PART_0_REPORT.md` 2.9 MB, `PART_8_REPORT.md` ≈300 KB (P10-08) | `reports/` is 329 KB total, no file over 120 KB; the validation generators already write into gitignored `artifacts/` (`scripts/validate_full_population.py:23-26`, `scripts/reconcile_validation_results.py:31`, `scripts/build_prompt8b_report.py:12-16`) |

So PART 10 here was **not** a re-run of the spec's `part10_cleanup.sh` (that script would
have deleted migrations this tree needs and overwritten improved guards). It was the
missing half: apply the deletions, then make every guard passable **on the tree a buyer
actually receives** — which is an export with no `.git`, and turned out to break two
guards that only worked in a checkout (§5.4, §5.6).

---

## 2. Commands, run in the working tree

The acceptance target as one command, on the final tree (`4756a5a` plus §7.2's compose
and doc follow-up), verbatim — `make verify-truth` is `Makefile:173`, and the target exits
0 only if every line below does:

```
$ PYTHONPATH=. make verify-truth
    python3 scripts/verify_frontend_backend_paths.py
        frontend/backend paths verified: 497 calls across 411 files match 1376 live routes
    python3 scripts/verify_no_dead_routers.py --strict
        route modules: helper=5, mounted=180
        no dead route modules and no filler markers
    python3 scripts/strip_generated_tails.py --check dashboard-next    => []
    python3 scripts/verify_no_filler.py                                 => []
    python3 scripts/verify_no_fake_success.py                           => []
    python3 scripts/verify_retired_references.py                        => []
    python3 scripts/verify_no_null_bytes.py                             => []
    python3 scripts/strip_padding_markers.py --check                    => {}
    python3 scripts/verify_docs_paths.py                                => []
    python3 scripts/verify_alembic_graph.py                             => []
    python3 scripts/check_repo_size.py                                  => []
    test ! -s scripts/fake_success_allowlist.txt
    test -z "$(git ls-files -- '.env' '.env.*' '*.env' ':!*.example')"
    python3 -m pytest tests/truth -q
        238 passed, 1 skipped, 7 warnings in 45.27s                    => exit 0
```

```
$ python3 scripts/verify_no_filler.py            => []
$ python3 scripts/verify_no_fake_success.py      => []
$ python3 scripts/verify_no_null_bytes.py        => []
$ python3 scripts/strip_padding_markers.py --check => {}
$ python3 scripts/verify_retired_references.py   => []
$ python3 scripts/verify_docs_paths.py           => []
$ python3 scripts/verify_alembic_graph.py        => []
$ python3 scripts/check_repo_size.py             => []
$ python3 -m compileall -q app services scripts tests alembic => exit 0
$ ruff check app tests scripts                   => All checks passed!
$ alembic heads                                  => 0091_call_debug_pcap_metadata (head)
$ python3 -m pytest tests/truth -q               => 238 passed, 1 skipped in 49.04s
```

(the final tree, after §5.6 and the regenerated pack; the same command mid-PART took
46.44 s, so the two follow-up commits cost nothing and changed no published number —
re-running `repo_stats.py --write-readme` on the final tree rewrote `docs/STATS.json` and
`README.md` with byte-identical content.)

Two commits came after this list was first captured (`36c0bee` §5.6 and `68d5153`), so
the guards over the files they touched were re-run on the final tree:
`verify_no_filler.py` → `[]`, `check_repo_size.py` → `[]`,
`ruff check scripts/pytest_collection_stats.py tests/truth/test_part9_packaging_sales_proof.py`
→ `All checks passed!`, `compileall` on the patched module → exit 0, and the cache path
the fix depends on was executed directly:
`measure_collected_pytest_nodes(Path("."))` → `8129`, the number `README.md` publishes.
Twice during this PART the same guard caught a real ordering mistake of mine, which is
worth recording as evidence that it works rather than as an aside: `sha256sum -c
dist/SHA256SUMS` failed on `reports/PART_10_REPORT.md` once while the report was still
being written, and failed again — `1 failed, 237 passed` — the moment §7.2's four
compose/doc files landed, because the pack hashes them too. Both times the fix was to freeze
the content and rebuild the pack, never to weaken the check.

The pack re-build that follows those commits is in `evidence/pack/SHA256SUMS` /
`dist/SHA256SUMS`, regenerated in the same commit as this file — and it was the
*report itself* that the guard caught as stale (`sha256sum -c dist/SHA256SUMS` →
108 OK, `reports/PART_10_REPORT.md: FAILED`), which is that check working exactly as
intended: a document inside a signed pack cannot be edited after the pack is built.

Size, measured with the same guard CI runs:

```
$ python3 scripts/check_repo_size.py --summary
{"ok": true, "file_count": 3686, "tree_bytes": 39027288, "tree_mib": 37.22,
 "target_mib": 40.0, "max_mib": 60.0, "within_target": true, "findings": []}
```

Before this PART the identical command reported 27 findings (18
`forbidden_archive_or_binary`, 8 `oversized_file`, 1 `tree_budget_exceeded`) and a
100,624,574-byte tree. Tracked files went **5483 → 3826**.

`tests/truth` detail, because the delta is the point: the baseline was
`23 failed, 214 passed, 1 skipped in 394.29s`; it is now `238 passed, 1 skipped` with
**zero failures**. Redis was available for the final run (`redis-server` 8.0.2
extracted from the Debian package into `~/localbin`, then
`PATH=/home/user/localbin/usr/bin:$PATH` so `tests/webhooks/conftest.py:16`'s
`shutil.which("redis-server")` probe succeeded). The same 23 failures were reproduced
without redis; no truth claim depends on redis.

---

## 3. P10-03, P10-05 — deletions

Commits: `5715fbe` (P10-03) and `f8629ea` (P10-05, 1654 paths removed). The complete
path list is `git show --name-only f8629ea`; every removed file was copied first to
`../voxdesk-archive-20261118/` (64 MB, mirrors the repo layout, outside the repository)
and then removed with `git rm -r --ignore-unmatch` + `rm -rf`.

Individually justified deletions, digests taken from the archived copy:

| Path | SHA-256 (first 16) | Bytes (du) | Lines |
|---|---|---|---|
| `app/api/pcap_routes.py` | `d793feed6183d0c1` | 12K | 247 |
| `FINAL_TEST_RESULTS.json` | `d740f92ae62c8fe9` | 4.0K | 7 |
| `tests_manifest.txt` | `f92d03aa01e68e71` | 368K | 4193 |
| `FILE_LIST.txt` | `8609103581b4ca21` | 56K | 1561 |
| `DOCKER_BUILD_SIMULATION.md` | `a4044d65514e0c0a` | 4.0K | 5 |
| `PROMPT2_REPORT.md` | `3ba9b34b7e1f36b8` | 20K | 160 |
| `CSS_HTML_TS_API_CONNECTION_REPORT.md` | `07c1503c0ae4c60d` | 60K | 164 |
| `services/media-plane/libvoxdesk_media.a` | `06517cbbcea823f7` | 64K | 99 |
| `scripts/live_local.env` | `4077c4c9f706f604` | 4.0K | 22 |
| `evidence/latency/2026-10-08/benchmark_200_calls.json` | `fc307d31b4096ac0` | 344K | 12114 |
| `evidence/latency/2026-10-08/benchmark_200_calls.csv` | `390ce01f3b9ad9fe` | 40K | 201 |
| `evidence/demo/01_inbound_call_audio.wav` | `ba52bc5877b45f84` | 40K | 194 |
| `evidence/demo/03_web_call_audio.wav` | `262d2db245dcac1d` | 32K | 167 |

Full digests: `sha256sum /home/user/voxdesk-archive-20261118/<path>`.

Groups removed under `f8629ea`:

- **`dashboard/` — 588 files, 3,547,848 bytes.** The retired Vite console. A guard that
  predates this PART already demanded its absence
  (`tests/truth/test_stats_single_source.py:55`:
  `assert not (ROOT / "dashboard").exists(), "the retired Vite dashboard is back on disk"`);
  it was 3.4 MB of the 40 MB budget; and the port it feeds is complete
  (`scripts/port_vite_pages.py` reports 0 pages left to port, and `scripts/ui_parity.py
  --check` after the deletion exits 0 with `vite routes: 116  DONE 112  PORT 0  DEAD 4`,
  reading the frozen snapshot `docs/ui_parity_routes.json`). No Make or CI target built
  it: after retargeting, `grep -rn "dashboard" Makefile Makefile.*.mk
  .github/workflows/*.yml` returns one help-text line about `verify-fe-be`, which
  describes `dashboard-next`. Two references had to move with the tree: the
  `npm audit (dashboard, …)` step in `.github/workflows/security-scan.yml`, whose
  `working-directory` would no longer exist, and `docs/DEMO/SCRIPT.md:33`, which invited
  a reviewer to open the flow builder in `dashboard`.
- **`docs/archive/` — 525 files.** `part0-reaudit/` (463), `part0-initial-evidence/`
  (38), `dashboard-static-mockups/` (16), plus the two `PART0_*.zip` nested archives.
  `docs/archive/history/` was deliberately kept: `scripts/repo_stats.py:397` and
  `scripts/repo_cleanup.sh:40` read it, and deleting it would have traded one broken
  guard for another.
- **`reports/` — 492 files** of per-run dumps (`part1/` 363, `part0-reaudit/` 48,
  `part0-clean/` 43, `part0/` 38). `reports/PART_<n>_REPORT.md` and
  `reports/*_SUMMARY.json` stay, as the spec requires: `README.md`,
  `scripts/collect_compliance_evidence.py` and `scripts/repo_stats.py` read them.
- **`uploads/` — 15 files** (the `SELL_PROMPT_*`/`SELL_CHECK_*` campaign dossiers) and
  **`audit/retell_*.md` — 4**: working notes, not product.
- **`artifacts/` — 4 polyglot dumps**, regenerated by `scripts/security_evidence.py:684`
  and now gitignored.
- **`evidence/latency/2026-10-08/` — 2 files**: mock-mode benchmark numbers filed as
  latency evidence. `tests/truth/test_part11_evidence_and_bench.py:163` already asserted
  this directory must not exist, because the same pair sits in `evidence/mock/` carrying
  `provider_fakes: true` and `not_product_performance: true`. Deleting it turned a
  failing guard into a passing one without touching the guard.
- **`evidence/demo/*.wav` — 2 files**: `evidence/demo/manifest.json` states the policy
  `GitHub Release assets only (no binary .wav blobs tracked in git)`; the tree violated
  it. Each trace keeps `audio_sha256`, and both archived files match their recorded
  digest exactly (`ba52bc58…`, `262d2db2…`), so the release asset stays verifiable from
  the JSON alone.
- **`app/api/pcap_routes.py` — 247 lines** (`5715fbe`, P10-03): it imported
  `PcapArtifact`, a model defined nowhere in `app/db`, and `app/main.py` never included
  it; mounting it would have raised `ImportError`. The retention branches that referenced
  the dropped model were already absent from `app/api/retention_routes.py` and
  `app/core/retention.py` (grep for `pcap` in both: no hits).

`app/api/outbound_call_routes.py` (87,453 bytes) was **not** touched, as instructed: it
belongs to PART 17B.

---

## 4. P10-06 — secret hygiene

`scripts/live_local.env` was tracked and carried live-looking values on five distinct
keys across seven lines: `SIP_GATEWAY_TOKEN` (7), `VOXDESK_LIVE_SPLUNK_HEC_TOKEN` (13),
`VOXDESK_LIVE_ELASTIC_API_KEY` (16), `VOXDESK_LIVE_DATADOG_API_KEY` (17),
`PGPASSWORD` (22). It is untracked; the file on the operator's disk is untouched, and
rotation is an owner action (§8), because a commit can untrack a file but cannot
un-leak a value.

`.gitignore` grew from three specific names to a rule set, because the old block is
exactly why the file got in: it matched `.env`, `.env.staging`, `.env.check` — names
*starting* with `.env` — while `live_local.env` *ends* with it.

```diff
 .env.*
+*.env
+!*.env.example
+!.env.example
+artifacts/
+*.a
+*.o
```

Verified in both directions with `git check-ignore`: `scripts/live_local.env`,
`.env.check`, `artifacts/x` and `foo.a` are ignored; `.env.example`,
`dashboard-next/.env.example`, `scripts/live_local.env.example`,
`.env.staging.example` and `.env.check.example` remain addable, since those are the
templates an operator copies from. `git ls-files | grep -E "\.env$"` → no matches.

The guard itself (`tests/truth/test_no_tracked_secrets.py`) got two changes:

1. `test_no_env_file_is_tracked` now also matches `path.endswith(".env")`, so the shape
   that actually escaped is caught. `Makefile:172` already ran
   `git ls-files -- '.env' '.env.*' '*.env' ':!*.example'`; the test was the weaker of
   the two.
2. `services/sip-gateway/tls.cfg:9` was a false positive:
   `private_key = /run/secrets/voxdesk-sip/tls.key` is a *mount path* the deployment
   fills in. The exemption is the narrowest thing that works — a value starting with
   `/` — and `test_an_inline_credential_in_a_cfg_is_still_a_finding` pins the boundary so
   the exemption cannot smuggle a key: an inline `-----BEGIN PRIVATE KEY-----` blob, a
   relative path and a `file://` URL all remain findings. The config file was neither
   deleted nor edited.

`.gitleaks.toml` lost the allowlist entry for `dashboard/tests/` (a tree that no longer
exists) and its header no longer claims `dashboard/src/` is scanned or cites
`AUDIT-REPORT.md`, a document this repository does not ship.

---

## 5. P10-07, P10-07b — guards, evidence artefacts and the lint surface

### 5.1 `evidence/demo/` — the acceptance command worked only by accident

PART 11's acceptance command is `jq -r '.provider_fakes' … evidence/demo/*.json`. On the
committed tree it printed `null` nine times: none of the eight traces, and not the
manifest, had the key at top level. Only `01_inbound_call.json` carried a flag at all,
buried at `execution_result.provider_fakes`. Silence is what a reviewer reads as "real".

All nine files now carry, at top level: `provider_fakes: true`,
`not_product_performance: true`, `evidence_tier: "LIVE_FAKE"`, and a `provenance`
sentence naming what produced them (the pipeline with STT/LLM/TTS and telephony stubbed
in-process; for six of them it also counts the `verified_by_tests` nodes that back the
claims). `manifest.json` says the same at the top level and its eight `trace_sha256`
values were refreshed. Nothing was upgraded to real: `docs/DEMO/SCRIPT.md:4` already
disclosed these as synthetic pipeline traces, and the JSON now states it in a form a
script can enforce.

```
$ jq -r '.provider_fakes' evidence/demo/*.json | sort | uniq -c
      9 true
$ cd evidence/demo && sha256sum -c SHA256SUMS
01_inbound_call.json: OK … manifest.json: OK        (9 of 9 OK)
```

### 5.2 `evidence/latency/2026-11-07/real_study.json` — dated, but not in a key anyone reads

The guard required `run_date` or `date`; the file only had `study_date`, so a dated
artefact presented as undated. `scripts/run_real_call_study.py` now writes both keys (at
both output paths) and the committed file gained the matching
`"run_date": "2026-11-07"`. Its honest emptiness is unchanged —
`status: NOT_RUN_MISSING_CREDENTIALS`, `calls_attempted: 0`, eight missing credentials
listed. `scripts/bench_latency.py`'s docstring also told readers mock evidence lands in
`evidence/latency/<date>/` while the code writes `evidence/mock/`; the docstring now
matches the code.

### 5.3 `evidence/real-integrations/2026-11-07/real_integrations.json` — regenerated by running the run

`test_the_committed_ledger_describes_its_own_run` failed because the ledger had
`providers` but no `run_status` and no `counts`: fourteen rows of
`SKIPPED_NO_CREDENTIALS` that a skimming reader could take for a provider run. Rather
than hand-edit JSON, the suite that writes it was run for real:

```
$ VOXDESK_REAL_INTEGRATION=1 VOXDESK_REAL_EVIDENCE_DIR=evidence/real-integrations/2026-11-07 \
  python3 -m pytest tests/real -q -rs
14 skipped in 0.09s
```

Resulting ledger: `"run_status": "NO_PROVIDER_RAN"`,
`"counts": {"total": 14, "pass": 0, "fail": 0, "skipped_no_credentials": 14, "other": 0}`,
`"provider_fakes": false` (true of this run: no fake was used, because nothing ran),
`"run_date": "2026-11-07"` preserved, `generated_at` moved to this run. No PASS was
invented, and `docs/EVIDENCE_STATUS.md` now prints
`NO PROVIDER RAN (14 entries, 0 PASS, all skipped for missing credentials …)`.

### 5.4 The docs had drifted from their generator, and one guard could only pass once

`docs/SALES/FEATURE_MATRIX_VERIFIED.md` claimed `PENDING_CREDENTIALS (2026-11-15)` on
three rows while the only ledger in the tree is dated `2026-11-07`; regenerating from
the committed evidence produced different bytes, which is exactly what
`generate_feature_matrix.py --check` is for. Two structural problems sat behind it:

- the render embedded ages relative to the day it ran (`Stale (>7d: 2026-11-07)`,
  `FRESH (3 day(s) old)`), so a committed document went stale merely by being read later;
- `tests/truth/test_part11_evidence_and_bench.py` asserted
  `matrix_main(["--check", "--reference-date", "2026-11-07"]) == 0` — a calendar date
  that happened to work the month the PART shipped. On any later day, and on a buyer's
  export of any age, that test cannot pass. A truth gate that decays is not a truth gate.

Fix, in `scripts/generate_feature_matrix.py`: `--reference-date evidence` resolves the
pin to the date recorded inside `evidence/junit/feature_evidence.xml`, so the check
compares the committed docs with the committed evidence and is green on any day; both
documents now print the reference date they were measured against (`Rendered with
reference date 2026-11-18 …`, and `- **Reference Date Used**: 2026-11-18 (the age above
is measured to this date, not to the day you are reading this file)`). CI keeps rendering
at today's date, so the 7-day window is still re-tested where the tools exist. The guard
now derives its pin from the shipped evidence, keeps the "one day past the window must
fail" case, and adds "evidence dated after the reference date must fail".

`evidence/junit/feature_evidence.xml` was regenerated with the command
`make verify-sale` uses (`Makefile:243`), with redis available:

```
$ TZ=UTC python3 -m pytest $(python3 -c "…feature_manifest.yaml…evidence_tests…") \
    --junitxml=evidence/junit/feature_evidence.xml -q
524 passed, 8 skipped in 60.51s            # 0 failures, 0 errors
$ python3 scripts/generate_feature_matrix.py --check --reference-date evidence
OK: … FEATURE_MATRIX_VERIFIED.md and … EVIDENCE_STATUS.md are fresh and in sync.   exit 0
```

The first attempt, without redis, produced `503 passed, 22 errors`; every error was
`Failed: Webhook API contracts require redis-server; install Redis first` from
`tests/webhooks/conftest.py:18` — the fixture's `shutil.which` probe, not a product
fault. The committed evidence is the redis-available run.

### 5.5 P10-07b — the 32 ruff findings

`ruff check app tests scripts` is in the acceptance list and reported 32 findings: 16
unused imports (F401/F811, removed) and 16 annotations naming types no module scope
could resolve. `app/services/contact_memory_service.py` had seven F821s for the same
reason: `get_settings`, `build_brief`, `set_brief` and `clear_brief` import
`MemorySettings` and `ContactBrief` from `app.db.channel_models` *inside the function*
(that tree imports this module's peers at module scope, so the lazy import is
load-bearing) while their signatures annotate with those names under
`from __future__ import annotations`. A `TYPE_CHECKING` block now names them for readers
and type checkers and the runtime imports stay; `app/ai/guardrails/safety.py` (2) and
`app/environments/service.py` (6) got the same treatment. The dead
`settings = await get_settings(...)` (F841) became a bare awaited call with the side
effect written next to it — reading the settings creates the default row the brief
depends on, so the *value* was dead but the *call* is not. `scripts/ui_parity.py`
imported `sys` twice twelve lines apart.

Proved by running what the edits touch: `232 passed` for
`tests/ai tests/environments tests/alerts tests/agent/test_colloquial.py
tests/agent/test_humanize_toggles.py tests/frontend/test_frontend_gates.py
tests/tenancy/test_region_pinning.py tests/deployment/test_polyglot_scope.py`, plus
`python3 -c "import app.main"` (1651 routes) and `compileall` exit 0. The two failures
in `tests/deployment/test_polyglot_scope.py` are pre-existing and belong to PART 17B:
they demand `services/{control-plane,signal-go,media-plane,ops}` move under
`experimental/` (P17B-02, audit finding S-15) and are not on G10's path —
`make verify-truth` does not run that directory. They are recorded here rather than
quietly "fixed", because deleting four service trees under a PART 10 commit would be the
same out-of-order edit that produced this mess.

---

### 5.6 Two export-only failures were a caching bug in the guards themselves

The first §10.4 export run (`git archive` of `c404577`, no `.git`) came back
`2 failed, 231 passed, 6 skipped in 1251.31s` while the same suite in the working tree
was green, and "the export is just slower" was not an acceptable answer — a truth guard
that cannot run on the tree a buyer receives is not a guard. Both failures traced to one
design defect, plus one too-tight budget:

- `scripts/pytest_collection_stats.py` cached the whole-suite collected-node count under
  a fingerprint that included `str(root.resolve())`. The cached value is the only thing
  that lets `tests/truth/test_docs_numbers_match_code.py` avoid re-collecting 8129 test
  nodes, so the checkout path being part of the key meant **no export, CI runner or
  buyer clone could ever reuse it**: each one paid for a full
  `pytest --collect-only` inside a 900 s ceiling, and on cold storage that is what
  raised `TimeoutError: pytest collection exceeded 900 seconds`
  (`scripts/pytest_collection_stats.py:154`) — an assertion about *how fast the machine
  is*, dressed as a statement about whether `README.md`'s numbers are true.
- `test_due_diligence_zip_and_sha256sums_verify` shells out to
  `make_due_diligence_pack.py`, which reaches for the same count, so its
  `@pytest.mark.timeout(300)` fired (`returncode: -9`) before the zip and its
  `SHA256SUMS` were ever compared — and the pack generator had refused nothing, it was
  simply killed.

Fixes, in `36c0bee` and `68d5153`: the checkout path is out of the digest and
`CACHE_VERSION` went 1 → 2 so every existing cache self-invalidates, while everything
that can actually change the count stays hashed (all `tests/**/*.py`, the pytest config
files, this helper, and the interpreter plus `pytest*` distributions). `.cache/` became
`.cache/*` with `!.cache/pytest-collection-stats.json`, because a negation cannot
re-include a file under an excluded directory, and the measured cache
(`{"schema_version": 2, "fingerprint": "b70f0bfc806deb4d…",
"collected_pytest_nodes": 8129}`) is now tracked so a fresh export can check its own
numbers without collecting anything. The pack test's budget is 900 s, with the
measurements that set it in the comment: 21.85 s standalone in an export with no `dist/`,
22.2 s for the generator in the same tree, and > 300 s for the identical test inside the
full suite on a host at load 6-7.

### 5.7 A dead `.gitignore` negation, found by counting tracked files

After the container recycle (§11) the tree was re-initialised and `git add -A` produced
**3817** tracked project files where `981e59e` had 3827. The ten-file gap was not lost
data: all ten are on disk under `reports/check/` (`.gitkeep`, `BACKEND_CHECK.md`,
`DOCKER_CHECK.md`, `FRONTEND_CHECK.md`, `OTHER_CHECK.md`, `SUMMARY.md` and the four
lowercase twins), and none of them could be added, because the ignore block read

```
reports/**
!reports/**/*.md
```

`reports/**` excludes the **directory** `reports/check/`, and git never descends into an
excluded directory, so the `!reports/**/*.md` line below it cannot re-include anything in
it — a negation that has never done anything. The files stayed tracked in the real history
only because they were force-added once; every fresh add since then silently drops them, and
`verify_no_tracked_artifacts.py` (which walks `git ls-files`) cannot see a file the index
does not hold. Fixed by re-including the directory, which is what makes the existing file
negations work, plus the `.gitkeep` inside it:

```
 reports/**
+!reports/check/
+!reports/check/.gitkeep
 !reports/**/*.md
```

Measured: `git check-ignore` now reports those ten as addable, `git ls-files | wc -l` is
**3828** at the repository root — the same tree the lost commits carried — while
`reports/check/summary.json`-style machine output stays ignored, which is the whole point of
the block. `verify_no_tracked_artifacts.py` → `PASS  no generated artifacts tracked (3827
files checked)`, `check_repo_size.py` → `[]`, and `docs/STATS.json` is unchanged
(`source_files 1959`), so no published number moved.

Same shape as the P10-06 `.cache/` fix in §4, reached from the opposite end: there a file
was *tracked* although the ignore rules should have covered it; here files were *untrackable*
although the negations promised otherwise. Both come from reading `git ls-files` instead of
reading `.gitignore`.

## 6. P10-09, P10-10 — published numbers, re-measured

`scripts/repo_stats.py --write-readme` and `scripts/make_due_diligence_pack.py
--allow-missing` regenerated `docs/STATS.json`, `README.md`,
`docs/SALES/PRICING_AND_TIERS.md`, `docs/SALES/LISTING_COPY.md`,
`docs/DUE_DILIGENCE/{README,KNOWN_LIMITATIONS,TEST_REPORT}.md`, the four
`sbom/*.cdx.json` files and `evidence/pack/SHA256SUMS`:

```
source_files            1912  -> 1959
source_physical_lines  442435 -> 449484
collected_pytest_nodes   8123 -> 8129
generated_date        2026-11-08 -> 2026-11-18
```

`tests/truth/test_stats_single_source.py` and
`tests/truth/test_docs_numbers_match_code.py` went from failing to passing on those
values. P10-10's other half was already true in `b95c5be` and was re-verified rather than
re-written: `scripts/verify_docs_paths.py` prints `[]` (no missing paths; the widget URL
and its header claim resolve), and the gate table in `docs/SALES/PRICING_AND_TIERS.md`
reads from the `gates` block of `docs/STATS.json`, which `repo_stats._gate_statuses()`
computes from the recorded JUnit evidence and the matrix counts —
`test_stats_single_source.py:60` asserts `"**GREEN**" not in pricing`. The pack build
disclosed rather than hid its gaps: five required evidence items are stale or missing,
listed verbatim in §8.

---

## 7. P10-11 — fresh export, no `.git`

```
$ git archive HEAD | tar -x -C /tmp/fresh          # HEAD = c40457781fc78ce1b226fd118571df9905703496
.git absent: clean export
files in export: 3826
bytes: 40926350

$ git archive HEAD | tar -x -C /tmp/fresh3         # HEAD = 36c0bee (after §5.6)
.git absent: clean export
files in export: 3827        # +1: .cache/pytest-collection-stats.json, now tracked
bytes: 40946449
```

In both exports, with no `.git` present (shown for `36c0bee`, captured verbatim):

```
$ python3 scripts/verify_no_filler.py              => []
$ python3 scripts/verify_no_fake_success.py        => []
$ python3 scripts/verify_no_null_bytes.py          => []
$ python3 scripts/strip_padding_markers.py --check => {}
$ python3 scripts/verify_retired_references.py     => []
$ python3 scripts/verify_docs_paths.py             => []
$ python3 scripts/verify_alembic_graph.py          => []
$ python3 scripts/check_repo_size.py               => []
$ python3 -m compileall -q app services scripts tests alembic => exit 0
$ ruff check app tests scripts                     => All checks passed!
$ alembic heads                                    => 0091_call_debug_pcap_metadata (head)
$ PYTHONPATH=. make verify-truth
    1 failed, 232 passed, 6 skipped, 7 warnings in 350.89s (0:05:50)
    FAILED tests/truth/test_part9_packaging_sales_proof.py::test_due_diligence_zip_and_sha256sums_verify
    E   Failed: Timeout >300.0s          (the @pytest.mark.timeout(300) that existed at 36c0bee)
```

For the record, the same command on the first export ended:

```
$ PYTHONPATH=. make verify-truth                   # /tmp/fresh, HEAD = c404577
    2 failed, 231 passed, 6 skipped, 7 warnings in 1251.31s (0:20:51)
    FAILED tests/truth/test_docs_numbers_match_code.py::test_documented_numbers_match_the_code
    FAILED tests/truth/test_part9_packaging_sales_proof.py::test_due_diligence_zip_and_sha256sums_verify
```


Both exports were taken the same way; the second one is the one that matters, because it
contains the fix for the failures the first one found. The six skips are the guards that
ask *what git tracks* (`_require_git()`), which correctly have no subject in an export
with no index — that is why the tree-walking guards (`check_repo_size.py`,
`verify_no_null_bytes.py`, the rest) are written to need no `.git` at all.

### 7.1 The export suite batched, because 1.9 GB is the real ceiling here

Re-exported at `c551f94` (3827 files, 40,954,869 bytes, no `.git`) and re-run: the eight
guards printed `[]`/`{}` exactly as above, and `make verify-truth` reached the pytest
stage and died at 86% with

    make: *** [Makefile:173: verify-truth] Killed
    dmesg: Out of memory: Killed process 16119 (python3) total-vm:954324kB, anon-rss:593728kB
    free -m: Mem: 1982 total, 757 available

That is the finding P10-07d was aiming at, stated correctly: after the marker was
widened, the export failure is **memory**, not time — the test's pack rebuild is a second
interpreter importing the app while the first still holds it. So the suite was run in
batches, each a fresh process, and every test in it passes:

```
$ for chunk in 3-way-split of tests/truth/test_*.py (27 files) + the two heavy files alone
  chunk_00  => 71 passed in 1.41s
  chunk_01  => 107 passed, 5 skipped in 42.40s
  chunk_02  => 44 passed, 1 skipped in 13.68s
  test_docs_numbers_match_code.py        => 7 passed in 13.28s
  test_part9_packaging_sales_proof.py    => 4 passed (after the pack build below)
  total: 239 collected — exactly the 232 passed + 6 skipped + 1 failed the single
         process reported, so nothing was skipped over by batching
$ python3 scripts/make_due_diligence_pack.py --allow-missing   # alone, no pytest parent
exit=0   dist/due-diligence-2026-11-18.zip (860,793 B) + dist/SHA256SUMS, 22 s
```

The same generator inside the test was SIGKILLed
(`CalledProcessError: Command '[…make_due_diligence_pack.py', '--allow-missing']' died with
<Signals.SIGKILL: 9>`) at 163 s — under the 900 s marker it is allowed to run that long,
and the kernel, not pytest, ended it. On any machine with ~2.5 GB free this test passes on
an export without help; the batching above is the proof that the assertion itself holds on
the shipped tree, and §8 no longer asks the owner to re-take it blind.

The memory ceiling is not export-specific and is worth stating for whoever runs this on a
small CI box: `make verify-truth` in the working tree completed in 49.42 s, and the same
command started while a PostgreSQL server was resident (1.9 GB host, ~760 MB free) was
killed at 60% with the same `make: *** [Makefile:173: verify-truth] Killed`. Nothing in
`tests/truth` needs a database, so the fix was to stop it and re-run — the target exited 0
again. If this suite ever fails only in CI, check RSS before checking the assertion.

**First export — `c404577`, before §5.6.** `make verify-truth` reported
`2 failed, 231 passed, 6 skipped, 7 warnings in 1251.31s (0:20:51)` and I initially
wrote that down as "this sandbox's cold filesystem hitting time ceilings". That
explanation was wrong in the way that matters: the pack test's 300 s marker did indeed
fire while rebuilding a pack that measures 21.85 s to build in the same tree, but
`test_documented_numbers_match_the_code` carries no marker at all, and its traceback ends
`TimeoutError: pytest collection exceeded 900 seconds` inside
`scripts/pytest_collection_stats.py:154` — the guard's own cache was unusable in an
export, for a reason that had nothing to do with the documents it was checking
(§5.6). Calling that "slow disk" would have shipped a real defect in the gate.

**Second export — `36c0bee`, after the cache fix.** Guards, compile, lint and the
migration head are byte-for-byte the same as before, and the suite drops to
`1 failed, 232 passed, 6 skipped, 7 warnings in 350.89s (0:05:50)` — 3.6× faster, with
`test_documented_numbers_match_the_code` now green on the export. The remaining failure
is the pack test's ceiling, which `68d5153` then widened with the measurements recorded
in its comment. Re-running `make verify-truth` on an export of `68d5153` or later is the
owner's last check (§8, item 4); it is not claimed green here, because this host could
not finish it — a single pytest process was enough to hold the load at 6-7.


---

## 7.2 The deletion had a tail: the dev stack still pointed at `dashboard/`

`verify_docs_paths.py` prints `[]`, `check_repo_size.py` prints `[]`, and
`test_stats_single_source.py:55` asserts `dashboard/` stays gone — none of which looked at
what *deployed* that tree. `docker-compose.yml` still defined

```yaml
  dashboard:
    image: node:20-alpine
    volumes: ["./dashboard:/srv"]          # a directory P10-05 deleted
    command: sh -c "npm ci … && npm run dev -- --host 0.0.0.0"
  nginx:
    depends_on: {…, dashboard: {condition: service_started}}
```

and `deploy/nginx.conf` still routed the dev entry point at it:
`upstream voxdesk_dashboard { server dashboard:5173; }` with `location / →
voxdesk_dashboard`. So after P10-05 the documented dev command
(`docker compose up --build`, `docs/DEPLOYMENT.md:16-18`) started a container whose bind
source did not exist, and every browser request to `/` went to a dead upstream. That is the
same defect class §1 is about, pointing the other way: the docs and the compose file
described a tree the repository no longer contains.

Fixed by removing the service and the nginx dependency, and pointing `location /` at
`voxdesk_api` — then re-rendering both files with the real Compose v2.32.4 binary:

```
$ docker compose -f docker-compose.yml config -q            => exit 0
  dev services now: api, db, nginx, redis, scheduler
$ docker compose -f docker-compose.prod.yml config -q       => exit 0 (with the 10 vars set)
$ python3 -m pytest tests/deployment tests/frontend -q      => 2 failed, 70 passed, 32 skipped
  (the identical pair as before the edit: the two P17B-02 failures of §5.5)
```

What this does **not** claim: that the dev stack boots. There is no docker daemon here, so
only `config` was executed — the files now resolve instead of naming a deleted directory,
which is a smaller statement than "works". The dev stack consequently has **no UI service**
(it points at the API, and under P17A-03 — `dashboard-next/next.config.js:75-79`, "ship a
Node server, not a static export" — the API has no export to serve). `docs/DEPLOYMENT.md`
now says exactly that and tells the reader to run `cd dashboard-next && npm run dev`; adding
a `next dev` service mirroring production's `web` would be the fix, but it changes
topology, so it is §8 item 3, not a PART 10 edit.

`.env.example` was the other tail. `docker-compose.prod.yml` interpolates 10 variables with
`${VAR:?…}`; 6 of them are not in `.env.example` at all (`POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`, `TRUSTED_HOSTS`, `GRAFANA_ADMIN_PASSWORD`,
`CONDUCTOR_WEBHOOK_SECRET`) and 2 are present-but-blank (`TELEPHONY_WEBHOOK_SECRET`,
`MEDIA_ENGINE_PUBLIC_IP`). Measured: `docker compose -f docker-compose.prod.yml config` with
only the copied template exits **15** on `services.db.environment.POSTGRES_USER: required
variable POSTGRES_USER is missing a value`, while with all 10 supplied it renders 503 lines
covering 11 services (`api backup caddy db grafana media-engine prometheus realtime-gateway
redis scheduler web`), 1 network, 7 named volumes, 5 health checks, `restart` on all 11 and
`depends_on` on 8. `docs/BUYER_DEPLOY_GUIDE.md` §3 used to end "the full key list with
comments is in that file", which is false for those 8; it now lists them instead.

One more measurement worth recording, because it explains every "flaky on a small box"
symptom in §7.1 including the two 15-minute crawls during this section: `/tmp` in this
sandbox is a **992 MB tmpfs**, so the exports, the `initdb` data directory and the Debian
packages I was unpacking lived in RAM (790 MB of it). `make verify-truth` then spent 14m40s
wall against 50 s of CPU and was killed mid-suite —
`Out of memory: Killed process 17948 (python3) total-vm:1202432kB, anon-rss:771644kB` at
`test_no_tracked_artifacts.py::test_the_cli_is_executable_end_to_end`, the test that spawns a
second interpreter. Deleting those scratch trees took available memory from 738 MB to
1226 MB and the same command finished in 44.84 s. If a truth guard here ever looks
non-deterministic, check `df -h /tmp` before believing the assertion.

## 8. Needs owner action (paste-ready, Git Bash)

Item 3 records what this snapshot did manage once the missing binaries were extracted
from Debian packages (`protoc`, `PostgreSQL`) — a blocked tool is not the same thing as a
blocked claim, and the difference only shows up if you try. The rest cannot be done from
this snapshot, and is not faked:

```bash
cd /path/to/voxdesk-call-ainul-islam/voxdesk-call

# 1. Rotate what the tracked scripts/live_local.env carried: SIP_GATEWAY_TOKEN,
#    VOXDESK_LIVE_SPLUNK_HEC_TOKEN, VOXDESK_LIVE_ELASTIC_API_KEY,
#    VOXDESK_LIVE_DATADOG_API_KEY, PGPASSWORD.
# 2. Refresh the five evidence items the due-diligence pack refused on. TWO of the
#    five are now closed by this PART — `python3 scripts/make_due_diligence_pack.py`
#    (no flags) refuses with exit 2 on three items, and its message no longer names
#    these two ("REFUSING to build the due-diligence pack: 3 required evidence
#    item(s) are missing or stale"):
#      reports/SECURITY_EVIDENCE_SUMMARY.json    DONE §8.6 — 7 of 8 scanners re-run
#                                               on 2026-11-18; trivy still needs CI,
#                                               and 108 checkov + 26 semgrep findings
#                                               are untriaged, so `make
#                                               security-evidence` exits non-zero by
#                                               design until someone reads them
#      reports/deploy_asset_validation.json      DONE §8.5 — helm/terraform/caddy/
#                                               kubeconform/compose, 25 checks, 0 fail
#    THREE remain, and none of them can be produced from this snapshot:
#      reports/TEST_EVIDENCE_SUMMARY.json        (19.76 days old, limit 14) needs the
#                                               full 8,142-node run — see item 3
#      reports/POLYGLOT_EVIDENCE_SUMMARY.json    (19.75 days, needs go + cargo)
#      evidence/staging                          (MISSING; needs a staging deployment)
    make verify-sale            # docker, redis, node, go, cargo: the summaries and the pack
    make staging-proof
    python3 scripts/make_due_diligence_pack.py    # must now succeed WITHOUT --allow-missing
# 3. P10-12, and what this environment could and could not measure.
#    DONE here, against a real PostgreSQL 17.11 (extracted from the Debian package,
#    initdb into /tmp/pgdata, port 55432) and a real protoc 3.21.12 + libprotobuf-dev:
    make contracts-check        # => OK: contracts compile; enum vocabulary matches
                                #    app.db.models; every operational message is
                                #    tenant-scoped.   (5 .proto files)
    alembic upgrade head        # => exit 0, 93 steps, 358 tables in public,
                                #    current revision 0091_call_debug_pcap_metadata
    alembic downgrade base      # => exit 0, 93 steps back to 1 table (alembic_version),
                                #    then upgrade head returned it to 358 — the whole
                                #    graph is reversible, not just forward-runnable
    docker compose -f docker-compose.prod.yml config
                                # => exit 15 on the copied template alone, because 6 of
                                #    the 10 ${VAR:?} keys are absent from .env.example and
                                #    2 are blank; exit 0 with all 10 supplied, rendering 503
                                #    lines / 11 services / 7 volumes / 5 health checks.
                                #    Compose v2.32.4, no daemon: `up` was never attempted,
                                #    and docker-compose.yml only renders since §7.2 removed
                                #    the service that mounted the deleted `dashboard/`.
#    STILL OWED, for one reason: the whole suite does not fit in 1982 MB. Re-attempted here
#    with /tmp emptied (1179 MB available at start) and it reached 9% in five minutes, then
#    sat with 27 MB available until it was stopped (PYTEST_EXIT=143); --collect-only on the
#    same selection died at exit 137, and the truth-suite OOM lines in §7.1/§7.2 are the
#    same wall one level down. So this is the suite's own peak RSS, not scratch space: run
#    it sharded (one process per top-level directory) or on a >=4 GB box, and keep /tmp
#    clear either way, since /tmp here is a 992 MB tmpfs and filling it is what turned a
#    45 s truth run into a 15-minute crawl.
    python3 -m pytest -m "not real_provider" -q
    python3 -m pytest tests/truth -q    # must stay 0 failed on the owner machine too
#    DECIDE, a topology call this PART deliberately left alone: since P10-05 the dev stack
#    has no UI service at all (`/` goes to the API, which serves a static export that
#    P17A-03 decided not to build). Either add a `next dev` service mirroring
#    docker-compose.prod.yml's `web`, or drop the single-origin dev URL from the docs.
#    docs/DEPLOYMENT.md records the limitation as it stands; it does not paper over it.
# 4. Nothing is owed on the export any more: §7.1 ran all 239 tests/truth tests green on
#    a `git archive` of the shipped tree, and the guards print []/{} there. The single
#    remaining difference between `make verify-truth` and the batched run is this host's
#    memory, so re-running the target verbatim on a normal box is a confirmation, not an
#    open question:
    git archive HEAD | tar -x -C /tmp/export && cd /tmp/export/voxdesk-call
    export PYTHONPATH=. && make verify-truth      # expect on an EXPORT: 233 passed,
#    6 skipped (239 collected; the six that ask what git tracks skip with no index, which
#    is 238 passed / 1 skipped as run in the checkout — §7.1's batch sums to the same 233)
#    Run it WITHOUT an extra `--timeout` override: the target passes none, and the
#    suite-wide default (300s) is what §5.6 changed one test away from.
# 5. Publish the branch the work is on:
    git push -u origin fix/part10-truth-reset
```

## 8.5 Deploy assets, validated with the real tools — and two bugs that came out of it

`make_due_diligence_pack.py` had been refusing on five evidence items (§6). One of them,
`reports/deploy_asset_validation.json`, turned out to be unblockable here once the tools
were fetched as static binaries — helm v3.16.4, caddy v2.8.4, kubeconform v0.6.7,
terraform v1.9.8, Docker Compose v2.32.4 — so `bash scripts/verify_deploy_assets.sh
--skip-tflint --json reports/deploy_asset_validation.json` was run for real:

    == result: PASS (0 failing check(s)) ==   checks_run: 25, failed: 0
      helm lint + template + kubeconform, both value sets  (prod 47 objects / 42 valid /
      5 skipped CRDs; staging 45 / 40 / 5), terraform fmt + validate on 13 roots and
      modules, `caddy validate` on all three shipped Caddyfiles, `docker compose config`
      on docker-compose.prod.yml and docker-compose.staging.yml

It did not start that way. The first run reported
`[FAIL] kubeconform:values-prod.yaml — error unmarshalling resource: … yaml: mapping
values are not allowed in this context`, and following that message produced two defects,
both now fixed:

1. **The chart.** `infra/helm/voxdesk/values.yaml` declared `tolerations: {}` for three
   components, while `values-prod.yaml` overrides `mediaEngine.tolerations` with a *list*.
   Helm cannot coalesce a table with a non-table, so every prod render printed
   `coalesce.go:286: warning: cannot overwrite table with non table for
   voxdesk.mediaEngine.tolerations (map[])`. The render still came out right (the prod
   list wins, and `{{- with }}` on an empty map is falsy — `cmp` confirms the rendered
   manifest is byte-identical before and after), but a Kubernetes `tolerations` field is a
   sequence, so the default's shape was wrong and the warning was permanent. All three
   became `tolerations: []`, `helm lint` passes both value sets, and stderr on the render
   is now empty.
2. **The guard.** `scripts/verify_deploy_assets.sh` ran
   `helm template … 2>&1` and wrote the merged result to the file it then validated, so a
   *warning* became a malformed manifest: kubeconform was handed
   `coalesce.go:286: warning: …` as YAML and reported a parse error, while the warning
   itself never reached a human. stderr is now separated, and a non-empty stderr fails the
   check with the warning quoted — the script's own rule ("a validation that did not run
   cannot be reported as a validation that passed") applied to warnings too. Reproduced
   twice before the fix, deterministic, and gone after it.

The `.env.check` question in §7.2 also resolved in the tree's favour: the six variables
`docker-compose.prod.yml` demands and `.env.example` omits are omitted *on purpose* (they
are per-deployment secrets), and `scripts/make_check_env.py` exists to generate them — it
wrote 85 keys at mode 0600 here, and with `.env.check` in place both prod and staging
compose files validate. §7.2's finding stands as a documentation claim (`.env.example` is
not the whole list), not as a missing mechanism, and `docs/BUYER_DEPLOY_GUIDE.md` now says
so in the same breath — and the guard then caught *me*: my first version of that paragraph
told the reader to run `make env-check`, a target that has never existed, and
`test_documented_numbers_match_code` failed with
`docs/BUYER_DEPLOY_GUIDE.md:73 make_target documented=env-check measured=defined target in
Makefile*`. That is the third time in this PART that a truth guard found a claim nobody had
checked, and the only difference between this one and the other two is that the author was
me. The paragraph now cites the script, which is what the validator's own error message
names.

## 8.6 The security evidence, refreshed by running the scanners

§8's list had one line for this: *"refresh `reports/SECURITY_EVIDENCE_SUMMARY.json`
— owner action, CI only"*. That delegation was wrong in the way this whole PART is
about. The tracked summary was 20 days old, produced by a CI run that wrote **two**
of the eight scanner artifacts, and every other column said `missing / no artifact
found`. Nothing about that requires GitHub: the scanners run on a laptop. So they
were run here, one at a time, with the flags CI uses, and the summary was
regenerated from the bytes that came out.

What ran, and what it said (all against this tree, 2026-11-18):

| Scanner | Version | Command shape (CI's) | Result |
|---|---|---|---|
| bandit | 1.9.4 | `bandit -c pyproject.toml -r app scripts -lll -f sarif -o …` | 0 findings at `-lll`, SARIF 554,507 B |
| pip-audit | 2.10.1 | `pip-audit -r requirements.txt -f json` | 86 advisories → **78** after the pins below |
| gitleaks | 8.21.2 | `detect --source . --config .gitleaks.toml` | 52 raw → **0** after the config fix; filesystem scan 114 → **0** |
| hadolint | 2.12.0 | `hadolint -f json` over all 8 Dockerfiles | 6 medium (`DL3008`×4, `DL3018`, `DL4006`) |
| checkov | 3.4.5 | `-d infra/terraform -d infra/helm --compact --quiet --output json` | 108 high (101 terraform, 7 secret-scan) |
| semgrep | 1.186.0 | `--config p/python --config p/typescript --config p/security-audit` | 26 (18 ERROR, 8 WARNING) over 1,830 files |
| npm audit | 10.8.2 | `--omit=dev --audit-level=high --json` in `dashboard-next` | 1 moderate (`next` 16.2.0–16.4.4), below the CI threshold |
| trivy | — | builds and scans the **production image** | not attempted: no Docker daemon here; CI's two trivy steps stay the only place it runs |

The regenerated summary now reads `artifact_count: 7`, `total: 219`
(`high 126, medium 15, unknown 78`), and its `overall` is still `BLOCKED` — for the
opposite reason to before. It used to be blocked because nothing had been measured;
now it is blocked because things were measured and are untriaged. `make
security-evidence` exits 1 (`--fail-on high` is the default), which is the gate
behaving correctly rather than a regression to apologise for.

**Five things that could not have reported a finding.** Each is the same shape: a
component that returned a tidy zero because the bytes never reached the code that
would have counted them. None was visible from the summary, which looked clean in
exactly those places.

1. **`.gitleaks.toml` was inert.** It declared `[[allowlists]]` (plural, an array of
   tables); gitleaks v8 reads one global `[allowlist]` table. Unknown keys in a
   config file are not an error, so scanning with the file and without it gave
   identical results — 52 findings, then 52 — which is how you can tell a security
   policy is decoration. The honest test of an allowlist is that the number moves.
   With the table renamed, the same scan drops to 5, and the remaining five are each
   provably not a secret: an ElevenLabs *voice-catalogue id* in `.env.check.example`,
   two `smoke-*` literals in `.github/workflows/helm-e2e.yml` (inputs to a
   throwaway kind cluster), the type name `Ed25519PublicKey` in
   `app/compliance_v2/audit_integrity/signing.py`, and a `key_digest` inside a
   Postgres idempotency transcript under `docs/evidence/`. The allowlist carries that
   reasoning per entry. It is also the reason `(^|/)tests?/` is written prefix-free
   rather than `^tests/`: this checkout's git root is one level above the project, so
   an anchored pattern silently matches nothing — the fixed `^\.github/…` entry had to
   be relaxed for exactly that reason, and 5 → 0 is the measurement that says so.
2. **`parse_hadolint` could only ever return 0.** It split the file on newlines and
   kept lines starting with `{`; `hadolint -f json` writes one single-line JSON
   *array*. Fixed to accept both shapes, with the JSON-array form first, because
   that is what `hadolint/hadolint-action@v3.1.0` writes.
3. **`parse_checkov` crashed on CI's output.** It assumed one `{"results": …}` object;
   checkov writes a *list* of per-framework reports, so the summariser raised
   `AttributeError` — the one failure mode that was at least loud. Both shapes are now
   counted across frameworks.
4. **CI's checkov artifact has never existed where the collector looks.**
   `--output-file-path` takes a *directory*: given
   `artifacts/security/checkov-results.json`, checkov creates that path as a
   directory containing `results_json.json`. So the very next line,
   `test -f artifacts/security/checkov-results.json`, cannot pass, and a filename-based
   collector could never classify `results_json.json` anyway. Reproduced with checkov
   3.4.5 on `infra/helm` before changing anything; the step now redirects stdout to
   the file and asserts `test -s`.
5. **`semgrep-results.json` was parsed as SARIF.** `KNOWN_SCANNERS` gives semgrep the
   kind `sarif` (CI's primary artifact), and the routing trusted that even for the
   `.json` sibling: no `runs` key → 0 findings → a `clean` line beside 26 real ones.
   `.json` artifacts are now sniffed for a `runs` array first, and semgrep's native
   report has its own parser.

All five are covered by `tests/truth/test_security_evidence_parser.py` (8 tests),
written against the exact payload shapes the tools emit, plus the corrupt-input case:
an unparseable artifact is reported as `unparseable`, never as a zero. That is the
same rule the rest of this PART applies to everything else — a check that cannot be
non-zero is not a check.

**Two pins moved, because they could.** `dnspython 2.9.0 → 2.9.2` (CVE-2026-128089)
and `pypdf 6.21.2 → 6.21.4` (seven advisories against the untrusted-PDF parser). Both
had published fixes at a patch level and no upstream constraint in the way, which is
what distinguishes them from the pillow residual that §7's R2b has to keep accepting
because `pipecat-ai` pins `Pillow<12`. The register did not grow. Verified by
`pytest tests/platform_v2/test_dns.py` (41 passed), `pytest tests/knowledge`
(21 passed), `pip check`, and `bash scripts/audit_dependencies.sh` going from
`FAIL: vulnerable dependencies outside the accepted-risk list: dnspython (1),
pypdf (7)` to `OK: no unexpected vulnerable dependencies.` — the script's own gate is
the proof, not the changelog entry.

**What was deliberately not done.** The 101 checkov AWS hardening findings and the 18
semgrep ERRORs are recorded, not triaged, and nothing was suppressed to make the
table look better: `CKV_AWS_23` (open ingress on a security group) and
`use-defused-xml` (Matrix/CALDAV XML paths) are per-deployment and per-product
judgements, and a suppressions list written by whoever is trying to close a report is
precisely how the gitleaks allowlist became a place to hide things. `next` was not
bumped, because a frontend bump you cannot build is not evidence. The 6 hadolint
warnings were not "fixed" either — pinning `apt`/`apk` versions in the runtime image
changes what the buyer's `docker build` resolves, and no Docker daemon exists here to
verify it.

Reproduce the whole thing on a fresh export (the four static binaries are the only
setup beyond `pip install -r requirements.txt -r requirements-validation.txt`):

```bash
pip install "bandit[sarif]" pip-audit semgrep checkov      # gitleaks, hadolint: static binaries
mkdir -p artifacts/security
bandit -c pyproject.toml -r app scripts -lll -f sarif -o artifacts/security/bandit-results.sarif
pip-audit -r requirements.txt -f json > artifacts/security/pip-audit.json   # exit 1 = advisories exist
gitleaks detect --source . --config .gitleaks.toml --report-format json \
  --report-path artifacts/security/gitleaks-results.json --exit-code 0
python3 - <<'EOF'
import json, subprocess, pathlib
rows = []
for f in sorted(p for p in pathlib.Path('.').rglob('Dockerfile*')):
    out = subprocess.run(['hadolint', '--no-color', '-f', 'json', str(f)],
                         capture_output=True, text=True, check=False).stdout.strip()
    rows += json.loads(out or '[]')
json.dump(rows, open('artifacts/security/hadolint-results.json', 'w'), indent=1)
EOF
checkov -d infra/terraform -d infra/helm --compact --quiet --output json > artifacts/security/checkov-results.json
semgrep --config p/python --config p/typescript --config p/security-audit --metrics=off \
  --json --output artifacts/security/semgrep-results.json app scripts dashboard-next
(cd dashboard-next && npm audit --omit=dev --audit-level=high --json > ../artifacts/security/npm-audit-dashboard-next.json)
make security-evidence    # exits 1 while high findings are untriaged: that is the gate
```

`artifacts/security/` is gitignored, so none of those bytes are committed — the
tracked artefact is the summary, which lists each artifact's `sha256` so a buyer can
re-run the scanner and compare hashes.

## 8.7 `make deps-check` was failing on a correctly installed tree

Restoring the pins that `pip install semgrep checkov` had moved (`pydantic 2.14.3 →
2.10.6`, `structlog 26.1.0 → 24.4.0`, `jsonschema`, `boto3`) cleared four
`[version]` problems and exposed the two that were never about my environment:
`make deps-check` reported **2 source-import failures on a clean install**, and
`make verify-sale` calls that target. Both reports were wrong, in opposite directions:

* **`bridge`** is not a distribution. It is the project's own
  `services/sip-gateway/bridge`, which `tests/telephony/test_sip_bridge.py` imports
  after `sys.path.insert(0, BRIDGE_DIR)`; a scanner importing from the repo root
  cannot see it, so the guard blamed `requirements.txt` for a module no
  `requirements.txt` should ever name. Fixed by classifying it in
  `LOCAL_PACKAGES`, where `app`, `bench`, `sdk` and the other first-party roots
  already live — not by making the scanner skip the file.
* **`imageio_ffmpeg`** is imported inside `try/except ModuleNotFoundError` with an
  `ffmpeg`-on-`PATH` fallback, in the same script that prints the error message.
  The scanner had a carve-out for exactly this situation — `loadtest/` was excluded
  from scanning because its `locust` import is "documented-optional" — but the
  carve-out was per-directory, so it also stopped *reading* the file. Now
  `guarded_import_names()` reads the `try` block: absence the importer handles is a
  `WARN … (guarded by except ImportError)`, absence it does not handle is still an
  error.

Current output, both directions proven:

```
requirements.txt: 53 pinned distributions
WARN  imageio_ffmpeg   imported by scripts/make_hold_music.py: No module named 'imageio_ffmpeg' (guarded by except ImportError)
WARN  numpy            imported by scripts/make_hold_music.py
WARN  port_vite_pages  imported by scripts/ui_parity.py
WARN  regex            imported by app/services/contact_memory_service.py
OK: all declared packages installed, importable and version-matched; no missing source imports.
```

A relaxation like this has to be shown to still catch the thing it is meant to
catch, so it was measured with a control: a file in `app/` containing an
unguarded `import definitely_not_a_distribution_xyz` made the same command report
`"ok": false` with `('definitely_not_a_distribution_xyz', 'missing')`; deleting the
probe returned it to `ok: true, errors: 0`. `tests/truth/test_dependency_guard_optional_imports.py`
(5 tests) pins the helper's four cases — guarded, tuple handler, unguarded, and a
handler that catches the wrong exception — so the distinction cannot quietly rot back
into "everything with a try block is fine".

---

## 9. What G10 does not certify

- P10-12's 8129-node run is not measured anywhere in this repository's evidence: the
  kernel OOM-kills it on this 1982 MB host at ~9% (`PYTEST_EXIT=137`), and
  `docker compose config` has no docker binary to run. The migration graph and the
  contract compilation ARE measured (§8 item 3), against real Postgres and real protoc.
- It is a *snapshot-truth* gate: tree == reports, guards green without `.git`, no
  oversized or misleading file shipped. It does not make the product production-ready and
  no sentence in this file says it does.
- Band A (`≈$30K`) additionally requires `G11` and `G17a`; Band B requires
  `G13`+`G14`+`G15`; Band C requires `G12`+`G16`+`G17b`+`G18`.
- `LIVE_PROVIDER` is granted to **no** feature. `docs/EVIDENCE_STATUS.md` says
  `NO PROVIDER RAN` because no carrier or AI credential exists here (§5.3), and the
  matrix caps every row at `LIVE_FAKE` accordingly. The demo traces are labelled
  synthetic (§5.1) and the mock benchmark is labelled not-product-performance (§3).
- `app/api/outbound_call_routes.py` is untouched, by instruction (PART 17B).
- `tests/deployment/test_polyglot_scope.py` still fails for the P17B-02 reason in §5.5;
  nothing here pre-empted that PART except the `dashboard/` tree, which G10's own guard
  and byte budget required (§3).

## 10. Commits

```
5715fbe P10-03:  delete the unmounted pcap route module
f8629ea P10-05:  delete nested archives, compiled objects, generated dumps and the retired Vite tree
e38bfa7 P10-06:  stop tracking generated environments and make the secret guard honest
b6b2c23 P10-07:  make the truth guards check the artefacts instead of contradicting them
2315a50 P10-07b: clear the 32 ruff findings the G10 acceptance gate counts
c404577 P10-09:  re-measure every published number after the deletions
14bfa32 P10-11:  write this report against the tree that now exists
36c0bee P10-07c: ship the collection-count cache so a fresh export can check its own numbers
68d5153 P10-07d: give the pack-verification test a budget a contended disk can meet
```

`14bfa32` and the two commits after `68d5153` exist only because the export run in §7
disagreed with the working tree, and the disagreement turned out to be a defect in the
guards rather than in the documents. That is the part of G10 the original report skipped:
it asserted acceptance from the tree it edited, never from the tree it shipped.

The list stops at `68d5153` on purpose. This file is inside the due-diligence pack, so the
pack is rebuilt after the report is frozen — and the commit that carries both can quote no
hash of itself: writing one here changes this file, which invalidates the pack hash, which
needs a new commit. `git log --oneline 14bfa32..HEAD` from the tip of the branch is the
complete answer, and `sha256sum -c dist/SHA256SUMS` → 109 OK / 0 failed is the proof that
this frozen text is the one that was hashed.

P10-00, P10-01, P10-01b, P10-02, P10-04 and P10-08 produced no commit because the tree
already satisfied them (§1); P10-10 rode along with `c404577`; P10-11 is this file plus
§7.1; P10-12 is §8 item 3.

---

## 11. Environment loss mid-PART, recorded rather than hidden

While §8.5 was being written the sandbox was recycled underneath the work, and the
difference is worth putting in writing because it changes what a reader can trust here:

```
$ git status                    => fatal: not a git repository (or any of the parent directories)
$ ls .git                       => No such file or directory
$ ls dist/                      => No such file or directory
$ ls .cache/                    => No such file or directory
$ python3 -c "import fastapi"   => ModuleNotFoundError: No module named 'fastapi'
$ python3 -c "import alembic"   => ModuleNotFoundError   (fixed for this session by `pip install alembic`)
```

What survived is the **content**: every file in `/home/user/voxdesk-call-ainul-islam/voxdesk-call`
is in the state the ten PART 10 commits left it, and everything deleted is still archived in
`/home/user/voxdesk-archive-20261118`. What did not survive is the **history** —
`.git` is excluded from the persisted workspace, and nothing had been pushed (§8 item 5 was
always an owner action). So `5715fbe … 981e59e` and the later commits exist now as subjects
and file states, not as objects, and the only durable copy of the real history is the one on
the owner's machine.

Two consequences a reader should not miss:

1. With no `.git`, the index-reading guards were checked rather than assumed, and they
   **fail safe**: `verify_no_tracked_artifacts.py` exits **2** with
   ``verify_no_tracked_artifacts: `git ls-files` failed: fatal: not a git repository … this
   check must run inside a git checkout``, and the five `tests/truth` tests that ask what the
   index holds *skip* instead of passing — which is why `make verify-truth` here reports
   `233 passed, 6 skipped` where a checkout reports `238 passed, 1 skipped` for the same
   tree. No guard went quietly green on an empty list. The repository was still
   re-initialised, because the tree is the deliverable and an index is what makes the
   tracked-artifact and tracked-secret checks mean anything; the baseline commit message
   names every PART 10 commit subject that was lost with `.git`. Re-base this tree on the
   owner's own `main` before publishing.
2. P10-07c's pre-warmed cache was tracked at `.cache/pytest-collection-stats.json`, and
   `.cache` is exactly the kind of path a snapshot or a `.gitignore`-driven tool drops —
   which is why it vanished here too. The durable half of that fix is the digest change (no
   checkout path in the key); the tracked file is a speed nicety. If it is missing, the
   guard re-measures and needs the time it was given in §5.6, nothing more.

After the recycle the Python environment was reinstalled from the repository's own pins
(`pip install -r requirements.txt -r requirements-test.txt -r requirements-validation.txt`,
which is what the missing `respx`/`python-hcl2`/`fakeredis` collection errors needed —
`requirements-test.txt:10-12`), and everything was re-run on the current tree: §8.5's
deploy-asset validation, `make verify-truth` → exit 0 with `238 passed, 1 skipped in 52.92s`,
`sha256sum -c evidence/pack/SHA256SUMS` → 109/109 OK, and a fresh `git archive` export whose
eight guards print `[]`/`{}` with `compileall` exit 0, `ruff` clean and one alembic head.

Two side effects of reinstalling are worth naming, because both are the reason this PART's
numbers exist. The first: with three test dependencies missing, `repo_stats.py` measured
`8088 tests collected, 6 errors`, wrote that into `docs/STATS.json` and `README.md`, and
`test_documented_numbers_match_code` failed with
`RuntimeError: pytest --collect-only failed; refusing to publish an unmeasured count` — the
guard would not launder a partial measurement into a published number. After the install the
count returned to the published `8129` with zero collection errors, and the docs were
rewritten to match. The second: `make verify-truth` on a fresh export of this tree got through **210 of 239
tests — 204 passed, 6 skipped, 0 failed** — before the 880 s ceiling I set on the call ended
it. Counting the progress characters rather than eyeballing the dots is the only reason that
sentence is safe to write: my first read of the same stream reported "1 F", and that `F` was
inside a `structlog` INFO banner, not a verdict. The run is the machine's limit, not a
result; §7.1's batched 239-green stands as the export proof.
