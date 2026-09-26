# Recordings

Recording metadata is a state machine on `call_recordings`. The existing `Call.recording_url` column is unchanged and is not treated as an access grant.

## States

`requested` → `recording` → `processing` → `ready`

Failure: `recording` or `processing` → `failed`

Deletion: `ready` or `failed` → `deletion_pending` → `deleted`

The same state reported twice is a duplicate and does not create another row. A later callback cannot move `ready` back to `recording`. A provider snapshot older than the local completion time is ignored.

## Consent

Consent categories are configuration:

- `all_party` requires a recorded `granted` state before capture starts
- `one_party` does not require a grant
- `unspecified` does not allow capture

This is not a legal determination and does not by itself satisfy any jurisdiction's telephone-recording statute. The category has to be set by the tenant. An unknown category is not treated as permission.

## Policy and retention

A missing policy row uses `Tenant.record_calls` and `settings.call_retention_days`. A saved policy can set enabled, retention days and a legal-hold flag. Retention days are an operator setting, not a claim about the law. Legal hold blocks deletion of that tenant's recording rows during the existing call purge. It is a flag, not a legal-hold system.

The daily purge in `app.core.retention` clears recording bytes and marks rows deleted before it deletes the call, except when legal hold is set.

## Storage and access

Provider media is not copied unless `TELEPHONY_MEDIA_DIR` is set. There is no claim of encryption at rest. When bytes are stored, the key is `tenant/<tenant_id>/recordings/<recording_id>` and the checksum is SHA-256 of those bytes.

Access requires the recording's tenant and `recording:read` on every request. A previous grant does not survive permission removal. The grant is an HMAC of tenant, recording and expiry, signed with the server secret. The secret is not in the token. The default lifetime is 120 seconds and cannot exceed 900. An expired token is rejected.

The grant is not a public object URL. Provider URLs, and any URL carrying a signature or bearer, are not returned. If the bytes were never copied, authorization can succeed and the body still says the media is not stored.

Each allowed or denied read writes an audit row with the recording id and the operation. The row does not contain the provider URL or a credential.

## Transcripts

A transcript job records provider `deepgram`, the configured model, language, status and attempt count. Text stays in `turns`. Completing a job without turns fails with `no_transcript` and does not invent text. Retry requeues a failed job up to three attempts and does not call Deepgram. Reading a job requires `transcript:read` and the owning tenant.

## Limits

- No certification of recording-law compliance, GDPR, HIPAA, SOC 2 or PCI
- No claim that provider-hosted audio has been deleted from Twilio, Telnyx or Vonage
- No permanent public recording URL
- No fabricated quality or transcript score
