# VoxDesk — Factual Repository Git History Note

This document records the factual commit history of the `voxdesk-call`
repository as preserved in `.git`. Per Rule `R11` and Gate `G9`, **no git
history has been rewritten, squashed, or force-pushed**; all historical commits
and the remediation of the October 3, 2026 NUL-byte corruption incident are
documented transparently below.

---

## 1. Chronological Commit Ledger (`git log --reverse`)

| # | Commit SHA | Timestamp (`+0600`) | Author | Subject | Summary of Changes |
|---|---|---|---|---|---|
| 1 | `bc2803bc821ef06af51fa63ad894c7e541da412c` | `2026-09-26 21:12:37` | Ainul Islam | `Initial commit for voxdesk` | Initial multi-service repository import: FastAPI backend (`app/`), Alembic migrations (`0001_baseline`–`0049_runtime_schema_alignment`), React dashboard (`dashboard/`), Next.js console (`dashboard-next/`), Go gateway/signaling/ops (`services/`), Rust control-plane and WebRTC media engine (`services/control-plane`, `services/realtime/media-engine-rs`), and C++17 DSP library (`services/media-plane`). |
| 2 | `1126370ce324097914be3642331024f37834ee57` | `2026-09-27 15:34:49` | Ainul Islam | `Update voxdesk-call with latest code` | Early backend route expansions, enterprise schema models, and dashboard views. |
| 3 | `b277fedbc32ad7076037f437114cd8c739e2f774` | `2026-09-29 18:30:30` | Ainul Islam | `Update backend routes, migrations, and test suites` | Additional API route modules, migrations, and unit/integration test files. |
| 4 | `ec589e435a240968a597f0dbb982efff0b4e178d` | `2026-10-01 07:55:58` | Ainul Islam | `Update full project codebase (backend and frontend)` | Full-stack synchronization of backend services and frontend components. |
| 5 | `573df5850648234f90ab8f9b6aa4062da30ac8c0` | `2026-10-03 11:44:05` | Ainul Islam | `feat: apply latest updates and fixes for voxdesk-call project` | **NUL-byte corruption incident** (see Section 2 below): 792 tracked files were accidentally overwritten with NUL-byte (`\x00`) binary payloads of matching byte length (`Bin N -> N bytes`), and an untracked `clone/` scratch directory was committed. |
| 6 | `dc85c3336f65fea8cc3c976b9c26b185bfa345b3` | `2026-10-07 20:45:13` | Ainul Islam | `feat: restore and reconcile 100% backend updates and e2e test suite` | **Remediation & PART 0 Truth Gate**: Restored all 792 NUL-corrupted files to valid UTF-8 source code, removed the `clone/` scratch directory and template filler routes, and added permanent CI truth guards (`scripts/verify_no_null_bytes.py`, `scripts/verify_no_filler.py`, `scripts/verify_no_fake_success.py`, `scripts/verify_retired_references.py`, `scripts/strip_padding_markers.py`, `scripts/strip_generated_tails.py`). |

---

## 2. The October 3, 2026 NUL-Byte Incident & Permanent Guard

### What Happened
In commit `573df5850648234f90ab8f9b6aa4062da30ac8c0` (`2026-10-03`), a broken
file-sync / archive extraction step on the developer workstation overwrote 792
files across `app/`, `tests/`, and `clone/` with zero-filled (`\x00`) byte
blocks of the same file size (`git show --stat 573df58` shows `Bin 2641 -> 2641 bytes`
on `app/voice/live_loop.py`, `app/webhooks/delivery.py`, etc.).

### How It Was Fixed
Rather than rewriting git history with `git filter-repo` or `git push --force`
(which would falsify the audit trail), commit `dc85c3336f65fea8cc3c976b9c26b185bfa345b3`
and the subsequent Gate G0–G9 closure series:
1. Restored every affected source file in place to valid UTF-8 source code.
2. Deleted the retired `clone/` directory (`scripts/verify_retired_references.py`
   blocks any reference to retired directories).
3. Added `scripts/verify_no_null_bytes.py` and `tests/truth/test_no_filler.py::test_null_bytes_and_accidental_empty_source_fail`,
   which scan every tracked and untracked text file in `git ls-files` and fail
   CI (`make verify-truth` and `make verify-sale`) if any file contains a
   `\x00` byte or an unexpected 0-byte body.

### Verification Command

```bash
python scripts/verify_no_null_bytes.py
# Expected output: [] (exit code 0)
```
