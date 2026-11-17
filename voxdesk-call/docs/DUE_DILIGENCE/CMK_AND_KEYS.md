# Customer-managed keys (CMK) and the key hierarchy

**Status: supported by the existing adapters, documented and exercised here; not
yet certified as "BYOK" in a contract.** This document says exactly what is
encrypted with what, what a customer key can and cannot do, and how to test the
claim locally — because "we support CMK" is a sentence that means nothing until
someone can point at the wrap/unwrap call.

## 1. Key hierarchy

```
customer KMS key (AWS KMS / Vault Transit)         ← the customer owns this
        │  wraps
        ▼
per-tenant DEK (32 random bytes, AES-256-GCM)      ← stored, wrapped, in the DB
        │  encrypts
        ▼
secret values (CRM tokens, SSO secrets, MFA seeds, webhook signing keys)
```

* **DEK per tenant, per purpose.** `app/security/secret_store.py` derives the
  encryption context from `{"tenant_id", "purpose"}`, so a DEK wrapped for one
  tenant's `crm` purpose cannot be unwrapped for another tenant or another
  purpose — the AAD makes the ciphertext non-transferable.
* **Algorithm**: AES-256-GCM with a random 96-bit nonce per encryption and no
  nonce reuse across the store; tampering fails the tag check rather than
  returning garbage.
* **Adapters**: one module, `app/security/kms/aws_kms.py`, holds
  `AwsKmsAdapter` and `VaultTransitKmsAdapter`; the protocol and the fail-closed
  `UnconfiguredKmsAdapter` live in `app/security/kms/__init__.py`, and
  `get_kms_adapter()` chooses by environment (`AWS_KMS_KEY_ID`, else
  `VAULT_ADDR` + `VAULT_TOKEN` + `VAULT_TRANSIT_KEY`, else fail closed).
  `LocalKeyRingKmsAdapter` is the deployment keyring used when no external KMS
  is configured — it must never be mistaken for a customer-managed key.

## 2. What a customer key protects — and what it does not

| Protected by the customer key | **Not** protected by it |
|---|---|
| CRM/calendar OAuth tokens | Call audio and transcripts (those are files/rows, not secrets; encrypt at rest with the storage layer) |
| SSO client secrets, SCIM tokens | Phone numbers and call metadata in the database |
| MFA (TOTP) seeds | Audit log rows |
| Webhook signing secrets, tool credentials | Anything the operator's own DB encryption covers |

Saying this plainly is the point: a CMK that silently does not cover recordings
would pass a checklist and fail an audit.

## 3. Tested steps (run these to verify the claim)

```bash
# 1. Envelope encryption, context binding, fail-closed behaviour, and the exact
#    request shape sent to AWS KMS and Vault Transit — 14 tests, no network.
pytest tests/security/test_cmk_adapters.py -q

# 2. The secret store seals and unseals with a tenant-bound AAD.
pytest tests/security/test_mfa_secrets.py -q

# 3. No default secret ships in the repository.
pytest tests/truth/test_no_default_secrets.py -q

# 4. Rotation re-wraps without changing plaintext.
python3 scripts/rotate_secrets.py --dry-run
```

A round trip through the real adapter API, for a manual check:

```python
import asyncio
from app.integrations.crm import crypto
from app.security.kms import LocalKeyRingKmsAdapter

ring = crypto.parse_key_ring("k1:" + crypto.generate_key())
kms = LocalKeyRingKmsAdapter(ring)
ctx = {"tenant_id": "demo", "purpose": "crm"}

async def main():
    dek, wrapped = await kms.generate_data_key(encryption_context=ctx)
    assert await kms.unwrap_data_key(wrapped, encryption_context=ctx) == dek
    try:                       # the AAD makes the blob non-transferable
        await kms.unwrap_data_key(
            wrapped, encryption_context={"tenant_id": "other", "purpose": "crm"}
        )
    except Exception as exc:   # KmsOperationError
        print("refused as expected:", type(exc).__name__)

asyncio.run(main())
```

## 4. Operational notes

* **Revocation**: destroying the customer key makes the wrapped DEKs
  unrecoverable. That is the feature — and it means *key deletion is an
  irreversible data-loss operation*. It must be a two-person action with a
  written record, and the incident response plan treats accidental deletion as
  SEV-1.
* **Rotation**: re-wrap DEKs under the new key version, keep the old version
  available until every wrapped blob is re-encrypted, then disable it. Never
  delete the previous version in the same change.
* **Availability**: if the customer KMS is unreachable, secret-dependent
  features fail closed (they do not fall back to an unencrypted store). The
  readiness probe reports the dependency rather than serving degraded security.
* **Backups**: a backup of the database without access to the customer key is
  useless for secrets — by design. `scripts/backup_target_check.py` refuses a
  backup whose destination contradicts the tenant's region pin; the key
  escrow question is a contractual one, and the answer today is "not escrowed".

## 5. Honest status

| Item | State |
|---|---|
| Envelope encryption with tenant/purpose AAD | Implemented and tested |
| AWS KMS adapter | Implemented; request shape asserted against an httpx mock transport (`tests/security/test_cmk_adapters.py`); **not** run against a live AWS account in CI |
| Vault Transit adapter | Implemented; same test approach and the same caveat |
| Local keyring adapter | Implemented and used by default — this is *not* a customer-managed key, and the matrix in §2 says so |
| Per-customer key in a contract | **Not yet** — no customer runs with their own key today |
| FIPS 140-3 validated HSM | Provided by AWS KMS / Vault Enterprise, not by us |
| Key escrow / break-glass | **Not implemented**; destroying the key destroys the secrets |
