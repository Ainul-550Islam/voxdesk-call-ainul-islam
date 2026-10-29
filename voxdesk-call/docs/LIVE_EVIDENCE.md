# Live-provider evidence

A test that runs against a fake transport proves the code path, not the
provider. Thirteen tests in this repository are marked `real_provider` and skip
by default; this document is how their absence is turned into evidence instead of
into a claim.

## The rules

1. **No network unless asked.** Every check is opt-in by group or by name.
   `python scripts/live_evidence_run.py` with no selection does nothing and
   exits non-zero.
2. **Credentials first.** A check whose environment variables are missing writes
   `status: "skipped"` naming the exact variables it needed. A skip is never a
   pass and never silent.
3. **Cost is capped before anything is spent.** Checks that can cost money run
   under `app.telephony.live_proof.cost_guard.CostGuard` — the same hard ceiling
   the live PSTN probe uses. A check that would breach the cap is aborted
   *before* the first billable call and recorded as `aborted`, with the guard's
   own report attached.
4. **The raw log is hashed.** Each result carries the SHA-256 of the raw output
   it was derived from, so the JSON cannot be edited without the log
   disagreeing.
5. **Read-only by default.** Nothing here buys a number, places a call, or
   writes to a carrier account unless the check says otherwise.

## Running it

```sh
python scripts/live_evidence_run.py --list              # every check and why it would skip
python scripts/live_evidence_run.py --group carriers    # Twilio, Telnyx, Vonage control planes
python scripts/live_evidence_run.py --group kms         # AWS KMS and Vault Transit
python scripts/live_evidence_run.py --group s2s         # speech-to-speech session handshake
python scripts/live_evidence_run.py --group sip         # SIP gateway registration
python scripts/live_evidence_run.py --group providers   # the repo's own -m real_provider suite
python scripts/live_evidence_run.py --check kms.aws-kms
python scripts/live_evidence_run.py --all --max-usd 2.00 --max-minutes 10
```

## Checks

| Check | Group | Requires | Proves |
|---|---|---|---|
| `twilio.control-plane` | carriers | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | the Twilio REST control plane answers for the configured account, read-only |
| `telnyx.control-plane` | carriers | `TELNYX_API_KEY` | the Telnyx call-control API answers for the configured account, read-only |
| `vonage.control-plane` | carriers | `VONAGE_APPLICATION_ID`, `VONAGE_PRIVATE_KEY` | the Vonage application API answers for the configured account, read-only |
| `s2s.handshake` | s2s | `OPENAI_API_KEY`, `GOOGLE_API_KEY` | a real speech-to-speech provider session module loads and opens with live credentials |
| `kms.aws-kms` | kms | `AWS_KMS_KEY_ID`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | AWS KMS generates, unwraps, and refuses a tampered data key |
| `kms.vault-transit` | kms | `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_TRANSIT_KEY` | Vault Transit generates, unwraps, and refuses a tampered data key |
| `sip.gateway-health` | sip | `VOXDESK_SIP_GATEWAY_URL` | the SIP gateway's `/healthz` answers and reports its registration state |
| `providers.real-suite` | providers | `VOXDESK_REAL_INTEGRATION` | the repository's own opt-in real-provider pytest suite |

## Output

One file per check under `evidence/live/`, schema `voxdesk.live_evidence/v1`:

```json
{
  "schema": "voxdesk.live_evidence/v1",
  "check": "kms.aws-kms",
  "status": "passed",
  "started_at": "…Z",
  "finished_at": "…Z",
  "commit_sha": "…",
  "raw_log": "evidence/live/kms.aws-kms.log",
  "raw_log_sha256": "…",
  "cost_guard": {"max_usd": 2.0, "spent_usd": 0.0, "max_minutes": 10}
}
```

`status` is one of `passed`, `failed`, `skipped`, `aborted`. A summary of the
whole run is written alongside them.

## How it reaches the sales material

`scripts/generate_feature_matrix.py` reads these files and only them. A feature
moves to `LIVE_PROVEN` when a live evidence file with `status: "passed"` exists
for it and is not stale. Every other status leaves the feature where it was —
`API_ONLY`, `NOT_CONFIGURED`, or `LIVE` at the level the offline suite can
support. There is no path by which a skipped check becomes a tick.
