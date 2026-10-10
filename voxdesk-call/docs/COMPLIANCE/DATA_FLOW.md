# VoxDesk Data Flow & Retention Architecture

> **Scope & Accuracy Notice**
> This document maps the actual runtime data paths, storage locations, encryption boundaries, and retention lifecycles implemented in the VoxDesk codebase. It describes **SOC 2-ready controls** and **HIPAA-ready configuration** capabilities; it does not claim any third-party certification.

---

## 1. End-to-End Runtime Data Flow Diagram

```text
+---------------------------------------------------------------------------------------------------+
|                                   EXTERNAL CALLER / USER                                          |
|              PSTN / SIP Carrier (Twilio, Telnyx)  |  Browser Web Call (WSS Protobuf)              |
+---------------------------------------------------+-----------------------------------------------+
                                                    |
                                  TLS 1.2+ / WSS (In-Transit Encryption)
                                                    |
                                                    v
+---------------------------------------------------------------------------------------------------+
|                               VOXDESK VOICE & MESSAGING RUNTIME                                   |
|                                                                                                   |
|  1. Ingress & Consent Gate (app/telephony/twilio_handler.py, web_transport.py, consent.py)        |
|     - Verifies carrier HMAC signature or single-use WebCall JWT                                   |
|     - Checks RecordingPolicy & RecordingConsent before any audio recording is requested           |
|                                                                                                   |
|  2. Live Pipecat Pipeline (app/agent/pipeline.py)                                                 |
|     - Audio frames (PCM16 / mu-law) streamed in-memory -> STT -> LLM -> TTS                       |
|     - Optional MonitorTap (app/agent/monitor_tap.py) fans out to authorized supervisors only      |
|                                                                                                   |
|  3. Synchronous PII Redaction Gate (app/gdpr/redact.py, app/ai/guardrails/pii.py)                 |
|     - When RecordingPolicy.redact_pii=True, scrubs Credit Cards (Luhn), SSNs, Emails, Phones,     |
|       DOBs, and Street Addresses BEFORE writing Turn rows or dispatching webhooks                 |
+--------------------+------------------------------+------------------------------+----------------+
                     |                              |                              |
                     v                              v                              v
+----------------------------+  +----------------------------------+  +-----------------------------+
|   OBJECT / MEDIA STORAGE   |  |    POSTGRESQL PRIMARY STORE      |  |    OUTBOUND INTEGRATIONS    |
| (app/telephony/recording.py|  | (app/db/models.py, crypto.py)    |  | (app/webhooks/delivery.py,  |
|  media_storage.py)         |  |                                  |  |  app/integrations/crm/)     |
|                            |  | - Calls, Turns, CallLatencyStat  |  |                             |
| - CallRecording metadata   |  | - RecordingConsent, AuditLog     |  | - SSRF-guarded HTTPS only   |
| - Signed short-lived read  |  | - AES-256-GCM sealed secrets     |  |   (app/core/ssrf.py)        |
|   grants (recording:read)  |  |   bound to (tenant_id, purpose)  |  | - HMAC-SHA256 signed events |
| - Purged on retention      |  | - Purged by app/core/retention.py|  |   (t=<unix>,v1=<hex>)       |
|   deadline unless held     |  |   per tenant/agent policy        |  |                             |
+----------------------------+  +----------------------------------+  +-----------------------------+
```

---

## 2. Data Inventory, Storage Locations, Protection & Retention

The table below reflects the exact tables, modules, and retention rules implemented in `app/core/retention.py`, `app/telephony/recording.py`, `app/telephony/recording_policy.py`, `app/gdpr/redact.py`, and `app/security/secret_store.py`.

| Data Category | Primary Code & DB Table(s) | Where It Lives | Protection at Rest & In Transit | Retention & Purge Mechanism |
|---|---|---|---|---|
| **Live Call Audio Frames (In-Flight)** | `app/agent/pipeline.py`, `app/telephony/monitor_bus.py` | Ephemeral process memory (and bounded Redis pub/sub only while an authorized supervisor session is active) | TLS/WSS in transit; never written to disk unless recording is enabled and consented | Discarded immediately after frame processing (sub-second lifecycle) |
| **Recorded Call Audio** | `app/telephony/recording.py` (`call_recordings`), `app/telephony/media_storage.py` | Configured media storage bucket (`storage_key`) + carrier recording until deleted | Signed time-bounded access grants (`grant_for`); requires `Permission.RECORDING_READ` + `AuditLog` entry on every read | Governed by `RecordingPolicy.retention_days` (default 90 days; configurable down to 0/disabled). Purged by `purge_for_calls()` unless `RecordingPolicy.legal_hold=True` |
| **Call Transcripts & Conversation Turns** | `app/db/models.py` (`turns`), `app/telephony/transcription.py` | PostgreSQL `turns.text` | Scrubbed before insert via `app/gdpr/redact.py` when `RecordingPolicy.redact_pii=True` | Purged by `app/core/retention.py::purge_expired_calls()` according to `RetentionPolicy` (`retention_days`, per-agent or per-tenant) |
| **Post-Call Summaries, Extracted Fields & QA Scores** | `app/db/models.py` (`calls.summary`), `app/qa/models.py` (`qa_evaluations`, `sentiment_results`) | PostgreSQL | Generated from PII-scrubbed transcripts when `redact_pii=True`; tenant-scoped queries | Purged alongside parent `Call` rows by `app/core/retention.py` or anonymized on GDPR erasure (`app/api/gdpr_routes.py`) |
| **Caller PII (Phone, Email, Lead Profile)** | `app/db/models.py` (`calls`, `leads`), `app/db/retell_models.py` (`contacts`, `contact_memory_entries`) | PostgreSQL | Tenant-isolated; never logged in plaintext in `AuditLog` (`app/audit/redaction.py`) | Subject to `RetentionPolicy` and immediate subject erasure (`DELETE /api/gdpr/subject` in `app/api/gdpr_routes.py`) |
| **DNC & Consent Provenance** | `app/db/enterprise_models.py` (`dnc_entries`), `app/telephony/consent.py` (`recording_consents`) | PostgreSQL | Immutable compliance decision records written transactionally with `AuditLog` | Retained as compliance proof of opt-out (`STOP` / DNC) and recording consent decisions |
| **Provider, CRM, Webhook, Identity & Tool Secrets** | `app/security/secret_store.py`, `app/integrations/crm/crypto.py`, `app/auth/identity/secrets.py` | PostgreSQL encrypted columns (`v1.<key_id>.<nonce>.<ct>` or `secret://encrypted/...` / `secret://kms/...`) | AES-256-GCM authenticated encryption with `(tenant_id, purpose)` AAD + optional AWS KMS / Vault Transit DEK wrapping (`app/security/kms/`) | Rotated via `scripts/rotate_secrets.py` (`--dry-run` / `--resume-file`) or endpoint secret rotation routes |
| **Authentication Credentials & MFA Factors** | `app/db/models.py` (`users.password_hash`), `app/auth/identity/models.py` (`mfa_factors`, `mfa_recovery_codes`, `user_sessions`) | PostgreSQL | Passwords hashed with `bcrypt`; TOTP seeds sealed via AES-256-GCM (`PURPOSE_TOTP`); recovery codes SHA-256 hashed; session tokens hashed | Sessions expire per `IdentityPolicy` (`session_max_age_minutes`, `idle_timeout_minutes`); revoked immediately on password reset or MFA disable |
| **Security & Governance Audit Trail** | `app/db/models.py` (`audit_logs`), `app/audit/service.py` | PostgreSQL `audit_logs` | PII/secrets redacted before write (`app/audit/redaction.py`); committed in the same DB transaction as the mutating action | Retained per tenant audit retention policy for forensic and compliance review |

---

## 3. Zero-Retention & PII-Redacted Operating Mode

Tenants handling regulated workloads can configure a minimal-persistence footprint:
1. **Disable Audio Recording**: Set `RecordingPolicy.enabled = False` via `PUT /api/recordings/policy` so `start_recording_if_enabled` never requests or stores audio files.
2. **Enable Synchronous PII Redaction**: Set `RecordingPolicy.redact_pii = True` so credit cards, SSNs, phone numbers, emails, dates of birth, and street addresses are replaced with `[REDACTED_*]` tokens before `Turn` persistence, post-call LLM analysis, regression-test generation (`app/services/regression_from_calls.py`), and outbound webhook delivery (`app/webhooks/call_event_bridge.py`).
3. **Short-Window Transcript Expiry**: Configure `RetentionPolicy` (`POST /api/retention/policies`) with a short `retention_days` window (e.g., 1–7 days) so `scripts/scheduler.py` purges expired call records automatically.
