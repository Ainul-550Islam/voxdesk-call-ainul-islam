# Runbook: TLS and signing-key rotation (`certificate-rotation.md`)

Two different rotations are covered here and they have different failure modes:

* **TLS certificates** (ingress, coturn, the media engine) — expire on a date and
  take the front door down if rotated badly.
* **Signing and encryption keys** (JWT signing, JWT *verification*, the envelope
  key ring that seals stored CRM/calendar/MFA/SSO credentials, webhook signing
  secrets) — do not expire, but a botched rotation either invalidates every live
  session or makes stored ciphertext unreadable.

Rule for both: **add the new material, verify both paths, then remove the old.**
Every step below is reversible until the last one.

## 1. TLS certificates

### 1.1 What is in front of what

| Listener | Where the certificate comes from |
|---|---|
| Public HTTPS (`api.ingress`, `frontend.ingress`) | `ingress.tls` in `values-<env>.yaml`: `secretName` + `hosts`. cert-manager, a cloud load balancer, or a manually created Secret — the chart renders whatever the values say and never generates keys. |
| coturn TURNS | `coturn.tls.secretName` (required when `coturn.tls.enabled=true`); mounted read-only at `/etc/coturn/tls` |
| WebSocket upgrades (`/ws`, gateway) | same ingress certificate; the gateway itself terminates no TLS |

### 1.2 Rotation, zero-downtime

1. **Inventory what is expiring**, including the chain:
   ```bash
   kubectl -n voxdesk get certificate                                        # cert-manager managed
   kubectl -n voxdesk get secret <tls-secret> -o jsonpath='{.data.tls\.crt}' \
     | base64 -d | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
   ```
2. **Stage the new certificate next to the old one** using a second secret name
   (`tls.secretName` → `<secret>-next`), then switch `ingress.tls.secretName` to
   it and `helm upgrade`. The old secret stays on disk until verification passes,
   so rollback is another `helm upgrade`, not a re-issue.
3. **Verify the posture, don't eyeball the padlock.** The repository's own
   checker refuses to disable verification and never prints key material:
   ```bash
   python3 scripts/verify_tls.py --url https://staging.example.com --websocket
   ```
   It reports scheme, handshake, validity dates, hostname match, chain, HTTP→HTTPS
   redirect, HSTS and the security headers, and the WebSocket upgrade probe. An
   unreachable endpoint is `BLOCKED`, never `PASS`; a plain-HTTP non-localhost URL
   is `FAIL`.
4. **Keep both certificates served for the overlap window** of at least one full
   client reconnect cycle (clients that pin a session ticket will not reconnect on
   demand). For coturn, restart the relay pods one at a time so live calls drain.
5. **Retire the old secret** only after `verify_tls.py` is green and the error
   rate over the overlap window is flat.

## 2. Signing keys

### 2.1 JWT signing and verification

`JWT_SECRET` is read from the Secret named by `secrets.existingSecret`
(`realtimeGateway.jwtSecretKey: JWT_SECRET` is that key's name; the chart never
renders a Secret with credentials in it). The
application refuses to start with the built-in default or with anything shorter
than 32 characters (`app/core/config.py` validates this at import time), which is
deliberate: a silent weak secret is worse than a crash loop.

Rotation that does **not** log every user out:

1. Generate the new secret (`openssl rand -base64 48`), never in argv.
2. Deploy the new secret to the **verification** side first: the platform accepts
   tokens signed with either the current or the previous key for one token
   lifetime.
3. Roll the API and scheduler so all replicas use it (`kubectl rollout restart`).
4. Only then remove the previous key from the accepted set.

A hard cut-over (replace and restart) invalidates every access token immediately.
That is acceptable in an incident — say so in the incident log, because every
agent in a call gets a re-auth prompt at the same moment.

### 2.2 Stored-credential envelope keys

Stored integrations are sealed in `v1.<key_id>.<nonce>.<ct>` envelopes with a
key ring: `CRM_ENCRYPTION_KEYS` for CRM/calendar/tool credentials and
`IDENTITY_ENCRYPTION_KEYS` for MFA and SSO material (format
`key_id:base64key,older_id:base64key`, first entry active — see
`app/core/config.py` and `app/security/secret_store.py`). A production
deployment without them fails its own startup validation.

Rotation is a **re-seal**, not a re-encrypt-in-place guess:

```bash
# 1. Prove every row still decrypts with the ring you are about to change.
python3 scripts/rotate_secrets.py --dry-run

# 2. Re-seal onto the new active key. Audited per row, resumable if interrupted.
python3 scripts/rotate_secrets.py --resume-file /tmp/rotate-$(date +%s).json | tee rotate.log

# 3. Only after the run completes, drop the old key from CRM_ENCRYPTION_KEYS /
#    IDENTITY_ENCRYPTION_KEYS and roll the API. Rows that were never re-sealed
#    would now be unreadable.
```

The script covers `CrmIntegration`, `CalendarIntegration`, `MFAFactor`,
`SSOConnection` and `AgentTool.secret_ref`, and writes an `AuditLog` governance
event per rotated row, so "who rotated what, when" is answerable afterwards.
`--dry-run` writing nothing is the point: a failed dry run means a key is missing
from the ring, and re-sealing then would destroy the only copy.

### 2.3 Webhook signing secrets

Per-tenant webhook secrets are stored sealed (same envelope mechanism) and are
rotated per tenant from the console or by the tenant's own API call. After
rotation, both the old and new secret are accepted for a short overlap; delivery
receipts in `webhook_deliveries` show whether the caller has picked up the new
secret yet. Backlog symptoms belong in [`webhook-backlog.md`](webhook-backlog.md).

## 3. Verification checklist (write the results down)

| Check | Command | Expected |
|---|---|---|
| TLS posture | `python3 scripts/verify_tls.py --url <url> --websocket` | `PASS`, expiry ≥ 30 days out |
| Certificate chain served | `openssl s_client -connect host:443 -servername host </dev/null 2>/dev/null \| openssl x509 -noout -issuer -dates` | issuer matches the planned CA |
| No plaintext secret in the release | `helm template … \| grep -c 'kind: Secret'` | chart renders no Secret with credentials |
| Envelope dry run | `python3 scripts/rotate_secrets.py --dry-run` | every row decrypts |
| Configuration self-check | `python3 -c "from app.core.config import settings; print(settings.validate_security())"` | `[]` (the same list `scripts/staging_certify.py` treats as a failed step) |

## 4. Write it down

Record: what was rotated, key ids (`key_id`, never the key), the overlap window,
the verification output, and the rollout time. A rotation with no record is
indistinguishable from an outage waiting to happen.
