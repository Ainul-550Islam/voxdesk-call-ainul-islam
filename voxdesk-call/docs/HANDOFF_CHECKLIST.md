# Buyer Handoff Checklist (`voxdesk-call`)

This checklist governs the technical, cryptographic, infrastructure, and operational transfer of the VoxDesk Voice AI Platform from Seller to Buyer. Every verification command cited below exists in this repository and can be run from a clean checkout.

---

## 1. Repository, Container Registry & CI/CD Transfer

- [ ] **GitHub Repository Transfer:** Transfer repository ownership or grant Buyer organization admin access; protect `main` with required status checks (`ci.yml`, `polyglot.yml`, `security-scan.yml`, `deploy-assets.yml`).
- [ ] **Verify Clean Working Tree & Tracked Artifacts:**
  - `python scripts/verify_no_tracked_artifacts.py`
  - `bash scripts/repo_cleanup.sh` (dry-run audit)
- [ ] **Container Registry Credentials:** Update `GHCR` / AWS ECR / GCP Artifact Registry push credentials in GitHub Actions repository secrets.
- [ ] **GitHub Actions Secrets Inventory:** Populate Buyer-owned secrets in Settings -> Secrets and variables -> Actions:
  - Core test/staging secrets: `JWT_SECRET`, `CRM_ENCRYPTION_KEYS`, `IDENTITY_ENCRYPTION_KEYS`, `TELEPHONY_WEBHOOK_SECRET`
  - Optional live-certification secrets (`.github/workflows/real-integrations.yml`): `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TELNYX_API_KEY`, `VONAGE_APPLICATION_ID`, `VONAGE_PRIVATE_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `AWS_KMS_KEY_ID`, `VAULT_ADDR`, `VAULT_TOKEN`

---

## 2. Domains, DNS, TLS & Edge Routing

- [ ] **DNS Records:** Point Buyer production and staging FQDNs (`A`/`AAAA` or `CNAME`) to the Caddy ingress or Kubernetes LoadBalancer.
- [ ] **Environment Domain Variables:** Set `DOMAIN`, `GRAFANA_DOMAIN`, `PUBLIC_BASE_URL` (`https://...`), and `CORS_ORIGINS` (explicit origin allowlist; never `*` in production) in the production environment file or Helm values (`infra/helm/voxdesk/values-prod.yaml`).
- [ ] **Validate Edge & Deploy Configs Before Cutover:**
  - `bash scripts/verify_deploy_assets.sh` (validates `Caddyfile`, `docker-compose.prod.yml`, Helm charts, and Terraform modules).

---

## 3. Cryptographic Keys, Secrets & KMS Rotation

- [ ] **Rotate All Application Secrets Fresh at Handover** (never reuse any pre-transfer or staging secret):
  - `JWT_SECRET` (>= 32 bytes cryptographic entropy; enforced at boot by `app/core/config.py`)
  - `TELEPHONY_WEBHOOK_SECRET` (high-entropy HMAC secret; enforced fail-closed)
  - `CRM_ENCRYPTION_KEYS` and `IDENTITY_ENCRYPTION_KEYS` (distinct AES-256-GCM keyrings formatted `k1:<base64url-32-bytes>`)
  - `SIP_GATEWAY_TOKEN` (shared bearer secret between FastAPI and `services/sip-gateway`)
- [ ] **Envelope KMS Backend (`KMS_PROVIDER`):**
  - Choose `aws_kms` (`AWS_KMS_KEY_ID`, IAM role/credentials) or `vault_transit` (`VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_TRANSIT_KEY`) for envelope data-key encryption.
  - Run `python scripts/live_evidence_run.py --group kms` to record signed KMS round-trip evidence under `evidence/live/`.

---

## 4. Telephony, Messaging & AI Provider Accounts

- [ ] **Twilio (Voice, Programmable SMS, WhatsApp):**
  - Transfer or provision Buyer Twilio subaccount; set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, and optional `TWILIO_WHATSAPP_NUMBER`.
  - Point Voice URLs to `${PUBLIC_BASE_URL}/telephony/twilio/voice` and Messaging URLs to `${PUBLIC_BASE_URL}/api/channels/webhook/sms` and `/api/channels/webhook/whatsapp`.
- [ ] **Telnyx (Call Control & Messaging):**
  - Provision Buyer Telnyx API v2 key, Ed25519 public key, Call Control `TELNYX_CONNECTION_ID`, and `TELNYX_MESSAGING_PROFILE_ID`; point inbound messaging webhook to `${PUBLIC_BASE_URL}/api/channels/webhook/telnyx`.
- [ ] **Vonage (Voice & SMS):**
  - Provision Buyer Vonage Application (`VONAGE_API_KEY`, `VONAGE_API_SECRET`, `VONAGE_APPLICATION_ID`, RS256 `VONAGE_PRIVATE_KEY`, `VONAGE_SIGNATURE_SECRET`, `VONAGE_FROM_NUMBER`); point inbound SMS webhook to `${PUBLIC_BASE_URL}/api/channels/webhook/vonage`.
- [ ] **SIP Trunking (`services/sip-gateway`):**
  - Configure `VOXDESK_SIP_GATEWAY_URL` and carrier SIP digest/IP-ACL trunks via `/api/v1/telephony/sip-connections`.
- [ ] **LLM, STT, TTS & Optional S2S Providers:**
  - Set Buyer API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`, `GOOGLE_API_KEY`).
  - Keep `ENABLE_S2S=false` unless Speech-to-Speech (`openai_realtime` / `gemini_live`) has been certified with Buyer credentials and budget caps (`S2S_SESSION_BUDGET_MICRO_USD`, `S2S_TENANT_BUDGET_MICRO_USD`).
- [ ] **Public Voice Demo (Optional):**
  - Keep `VOICE_DEMO_AGENT_ID` unset if no unauthenticated marketing demo is desired (endpoint fails closed with `503`); otherwise point `VOICE_DEMO_AGENT_ID` to a dedicated sandbox agent with `VOICE_DEMO_RATE_LIMIT_PER_MINUTE` and `VOICE_DEMO_MAX_TURNS`.

---

## 5. Data Stores, Backups & Disaster Recovery

- [ ] **PostgreSQL 16+ & pgvector:** Run `make migrate` (`alembic upgrade head`) and confirm single head (`0069_voice_demo_sessions`).
- [ ] **Redis 7+:** Verify persistence/AOF settings and private network binding (no public host port in `docker-compose.prod.yml`).
- [ ] **Off-Host Encrypted Backups:** Configure `RCLONE_REMOTE` for S3/R2/GCS replication and schedule `scripts/backup.sh`; verify restore readiness with `scripts/restore.sh` and `scripts/dr_drill.sh`.

---

## 6. First-30-Days Buyer Operations Runbook

- [ ] **Day 1 — Clean Build, Contract & Migration Verification:**
  - Run `make verify-truth verify-hygiene verify-fe-be verify-deploy-assets`.
  - Run `make staging-proof` on a Docker host and archive `evidence/staging/<date>/staging_proof.json`.
  - Create initial Buyer owner account via `python -m scripts.create_owner --help`.
- [ ] **Day 7 — Live Carrier & Provider Certification:**
  - With Buyer credentials loaded, run `python scripts/live_evidence_run.py --all --max-usd 0.50` and inspect `evidence/live/*.json` (see `docs/LIVE_EVIDENCE.md`).
- [ ] **Day 14 — Observability & Disaster-Recovery Drill:**
  - Run `python scripts/verify_observability.py` against live Prometheus/Grafana (`observability/alerts.yml`, `observability/slos.yml`).
  - Execute `sh scripts/dr_drill.sh` against a scratch database (`RESTORE_TARGET_DB=voxdesk_restore_drill`) and record RTO/RPO.
- [ ] **Day 30 — Post-Cutover Audit & Due-Diligence Snapshot:**
  - Confirm Seller has zero residual credentials, SSH keys, or webhook tokens.
  - Run `python scripts/generate_feature_matrix.py` and `python scripts/make_due_diligence_pack.py` to archive the Buyer's baseline compliance and operational evidence bundle.
