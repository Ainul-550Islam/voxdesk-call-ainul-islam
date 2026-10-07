"""Deliver complete current PART 0 files with scoped, measured acceptance evidence."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / 'reports/part0-clean'
files = json.loads((E / 'complete-files.json').read_text())
results = json.loads((E / 'PART_0_TEST_RESULTS.json').read_text())
report = ROOT / 'reports/PART_0_REPORT.md'
archive = ROOT / 'reports/PART_0_FULL_CODE_AND_EVIDENCE.zip'
text = f'''# PART 0 — Authorized truth-reset closure

Date: 2026-10-07 (Asia/Dhaka)

## Result

**PASS for the revised, documented PART 0 executable gate. PART 1 has not started.**

The original literal-reference grep and removals-only contract clauses are not claimed
satisfied verbatim. Their conflicts with preserving historical evidence and the newly
authorized repairs are explicitly resolved in `reports/part0-clean/ACCEPTANCE_SCOPE.md`.
This is a scoped acceptance result, not a claim that the entire product is error-free or
production-ready.

| Current check | Actual result |
|---|---|
| make verify-truth | PASS |
| Truth tests | 41 passed, 0 failed/errors/skipped |
| Affected backend regression population | {results['unique_passed']} distinct passing tests, 0 final failed/errors/skipped |
| Broader regression command | 347 passed; the later truth rerun adds one new contract-snapshot test |
| Next dashboard | 81 passed, 0 failed/skipped; typecheck and production build PASS |
| Global configured Ruff / compileall | PASS / PASS |
| Contract compiler | PASS, five protobuf files |
| Current OpenAPI | Exact snapshot test PASS; identical across Python hash seeds 0 and 1 |
| Alembic | One unchanged head: 0049_runtime_schema_alignment |
| Production and full Compose configuration | Both PASS; validation inputs only, no services deployed |
| Active retired-engine references | Zero findings |
| Null-byte / padding / filler / fake-success guards | PASS within their declared scopes and existing shrink-only facade register |
| Full backend collection | 4,234 current nodes; collection is not full-suite execution |
| Registered app routes | 1,156, versus 1,210 before this authorized cleanup and 1,669 before original PART 0 |

## What changed

1. **Actual repetition removed, not the detector weakened.** Conductor's route-specific
   permission checks and service calls now share one error/transaction boundary. The
   real router remains registered. Lead models share identical mandatory scope columns;
   nine PostgreSQL table definitions, constraints, defaults and indexes compare unchanged
   apart from declaration order. Existing lead and Conductor security/E2E tests pass.
   The 0.35 ratio and 200-code-line threshold are unchanged; no new filler exemption exists.
2. **Six synthetic extension islands removed.** Each had nine generic routes returning
   static health/stats/list/bulk/cache results plus unused helper functions. Full-file AST
   dependency inspection found no incoming references from retained code. Only these
   54 paths/helpers were removed; real KB, PCAP, phone, recording, tool and workflow APIs
   remain. The original 95 deleted clone modules and three removed filler trees stay removed.
3. **Audit integrity repaired.** Call search/replay and audited policy/DNC changes use the
   existing durable audit ledger. Mutation events are staged before the business commit.
   A real database regression injects audit failure and proves the DNC mutation does not
   commit. Successful mutations persist both records with bounded/redacted details.
4. **Legacy agent-test transport fails closed.** It no longer creates process-local sessions,
   invented tokens, unregistered WebSocket URLs or constant latency values. Agent ownership
   is checked against the real database before returning 501. Foreign/missing agents and
   nonexistent sessions return 404. Existing persisted simulation APIs remain separate and
   unchanged. No implementation of live WebRTC is claimed.
5. **QMS facade made truthful.** Veeva, MasterControl and ETQ credentials never establish
   connected=True. Health explicitly distinguishes missing configuration from an unsupported
   transport. Document/finding/traceability/audit-package operations return typed 501 errors
   rather than empty success or invented records. HTTP regressions verify the real error
   response and cross-tenant 404. No vendor network request was manufactured.
6. **UI false-green behavior removed.** The connection diagnostics no longer turn
   401/403/422 into success or swallow compliance API errors into an empty successful list.
   Pending, failed and successful operations retain distinct states. Five browser-component
   regressions cover 401, 403, 422, 500 and actual successful responses. Static build-success,
   line-count and capability claims were removed from the affected shadow dashboard pages.
7. **Export/replay truth tightened.** Export field defaults are sorted, eliminating
   process-hash-order contract drift. Replay no longer invents a signed recording token;
   canonical recording management remains responsible for authorized access.
8. **Permanent verification strengthened.** Added the active-reference guard with
   line-specific denylist exceptions only, its adversarial regression tests, and exact
   OpenAPI snapshot checking. Updated route snapshot, measured README and conservative
   generated feature matrix. No LIVE claim was added without qualifying evidence.

## Debugging evidence and no artificial green

The first new HTTP QMS test used an incorrect fixture import; that test error was repaired.
The wider E2E rerun then caught a nested call-search audit invocation that still used the
old helper signature. It was fixed, neighboring tests rerun, and the whole selected broader
regression command rerun successfully. Original failing logs/XML remain in the archive.
No existing assertion was loosened, no failing node excluded, and no skip/xfail introduced.
The route-count snapshot was updated only for the documented 54 synthetic route removals.

The 348 unique passing backend IDs are recorded in `PART_0_TEST_RESULTS.json`, with measured
per-test durations and evidence references. Do not add 347 and 41: forty truth tests overlap.
The remaining backend population was collected but not executed by this scoped pass.
Previous global validation ledgers are historical, not a current all-green certification.

## Guard-policy boundaries

The facade allowlist still contains the pre-approved unresolved future-part files and
remains shrink-only. It was not expanded to absorb the nine new findings. Historical
archives intentionally contain the old claims and deleted source for due diligence.
An unrestricted grep will find those records and the guard's own prohibited-name tuples;
its output is not falsely described as empty. The active-reference checker excludes
forensic material and exempts only exact declaration lines, not entire guard files.

The original OpenAPI removals-only expectation predates the authorized security and
truth-contract corrections. Those corrections are documented rather than disguised as
removals. Current generation is deterministic and permanently checked against the reviewed
snapshot. No migration was changed to force schema agreement.

## Remaining work outside this PART 0 gate

This pass does not prove live carrier/media/provider operation, finish the known facade
register, clear all historical Python-wide typecheck diagnostics, or establish compliance
certification. QMS and legacy agent-test transports are explicitly unavailable, not complete.
Deprecation and build warnings remain in the raw logs. The full hosted polyglot CI matrix
and full 4,234-node backend execution were not rerun here. No production deployment occurred.
PART 1 remains a separate task and has not been started.

## Complete file contents

All current PART 0 source/config/test/document files below are reproduced first-to-last
line. No patch-only substitutes or shortened source bodies are used. Binary historical
backups are included losslessly in the companion archive. The previous blocked report,
source bundle and pre-repair files are preserved in the authorized-cleanup archive.
The report itself is not recursively embedded inside itself.

'''
for relative in files + ['reports/part0-clean/PART_0_TEST_RESULTS.json']:
    path = ROOT / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix == '.zip':
        text += f'### Binary: {relative}\n\nSHA-256: `{digest}`. Complete bytes are included in the archive.\n\n'
        continue
    body = path.read_text()
    fence = '`' * max(4, max((len(m.group()) + 1 for m in re.finditer(r'`+', body)), default=4))
    text += f'### File: {relative}\n\nSHA-256: `{digest}`\n\n{fence}\n{body}' + ('' if body.endswith('\n') else '\n') + fence + '\n\n'
text += '## Current command evidence\n\n'
for name in ['make-verify-truth.log', 'truth.log', 'contracts.log', 'heads.log', 'compose.log', 'ruff.log', 'repo-stats.log', 'next-tests.log', 'next-types.log', 'next-build.log']:
    path = E / name
    if path.exists():
        text += f'### {name}\n\n````text\n{path.read_text()}\n````\n\n'
report.write_text(text)
entries = set(files + ['reports/PART_0_REPORT.md'])
for directory in [ROOT / 'reports/part0', E]:
    entries.update(str(p.relative_to(ROOT)) for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for relative in sorted(entries):
        z.write(ROOT / relative, relative)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert len(z.namelist()) == len(set(z.namelist()))
    for relative in files:
        assert z.read(relative) == (ROOT / relative).read_bytes()
print(json.dumps({'complete_files': len(files), 'archive_entries': len(entries),
                  'report_bytes': report.stat().st_size, 'archive_bytes': archive.stat().st_size}, indent=2))
