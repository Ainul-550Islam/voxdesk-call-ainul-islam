# Enterprise telephony

This boundary sits beside the existing call engine. `app/telephony/twilio_handler.py`, outbound dialing, IVR, transfer and the media stream are unchanged. New provider, number, recording, transcript, QoS and callback work goes through the modules below. It is not on the live voice path.

## Providers

Supported names are `twilio`, `telnyx` and `vonage`. Selection is server-side. A client may name a provider. It cannot send a secret, and an unknown name is rejected.

Twilio is enabled for a tenant that has no binding, so existing outbound and webhook behavior keeps working. Telnyx and Vonage stay disabled until a binding row enables them. A disabled provider is rejected. Credentials live in environment settings, not in tenant rows, logs or API responses.

`health` reports whether credentials are present. It does not probe the provider and it does not mean the adapter is production-ready.

### Twilio

The adapter calls the existing redirect client and transfer TwiML for live transfers. Webhook checks call `verify_twilio_request` and do not reimplement the HMAC. Number search, purchase, release and recording use the Twilio REST client when the SDK and credentials are present. If either is missing, the adapter raises a typed error and does not invent an id.

Confirmed capabilities come from configuration: voice and recording when the account is configured, SMS when `TWILIO_PHONE_NUMBER` is set, WhatsApp when `TWILIO_WHATSAPP_NUMBER` is set. Transcription is not a Twilio capability here. Deepgram remains the STT engine.

### Telnyx

The adapter calls Telnyx API v2 for number search, number orders, release, Call Control create/hangup/transfer, and record start/stop. A missing API key or connection id is a configuration error. HTTP 401, 403, 404, 409, 422, 429 and 5xx map to typed errors. WhatsApp is not implemented.

Webhook verification expects `telnyx-signature-ed25519` and `telnyx-timestamp`, rejects timestamps outside a five-minute window, and rejects when the public key or PyNaCl is absent. A missing verifier is a rejection, not an acceptance.

Telnyx is not production-ready until credentials are set and a real account call has been run. This repository does not claim that.

### Vonage

Number search, buy and cancel use the REST number API when the API key and secret are set. Voice calls need an application id and a private key, and this runtime does not sign RS256. Those operations fail with a configuration or unavailable error. Recording and WhatsApp are unsupported. They do not return a fake call id.

Webhook verification uses the signature secret. HS256 bearer JWTs are checked with a constant-time compare and an `exp`/`iat` window. A missing secret is a rejection.

Vonage voice is not production-ready.

## Numbers

States are `available`, `reserved`, `provisioned`, `assigned` and `released`. Search does not write a row. Provision inserts `reserved` before the provider call so two tenants cannot hold the same active number. The unique key is the E.164 value, not the provider. A failed provider call releases the reservation. Assign requires a confirmed capability. Release calls the provider when an external id exists, then clears the active key.

Purchase checks the existing voice-minute entitlement. It does not create a second meter and it does not invent a provider price. A denied subscription cannot buy a number. Unknown provider price stays unknown.

## Callbacks

`telephony_callback_events` stores a hashed idempotency key. A duplicate delivery does not apply the effect again. Out-of-order call updates go through `app.telephony.call_state`, which refuses to move a terminal call backward. Reconciliation ignores a provider snapshot older than the local terminal timestamp and never moves a number to another tenant.

Permanently failed callbacks use the existing `DurableJob` table with job type `telephony.callback`. The payload is the event id, provider, type, attempt count and error class. Secrets and recording URLs are dropped. Replay requires `security:settings`, stays in the tenant, and is limited to three attempts. `replayed` means the handler finished. `accepted_for_retry` means it was only queued again.

The existing Twilio status webhook is still the live path. It was not rewritten. Platform reconciliation is a separate service and is not called from the media stream.

## Recording, transcription, QoS

See `docs/RECORDINGS.md` for recording, consent, storage and deletion.

Transcript jobs point at the existing turn rows and Deepgram configuration. They do not store a second transcript and they do not call Deepgram themselves. A job completes only when turns already exist.

QoS stores packet loss, jitter, RTT, latency, duration and disconnect or media errors when the provider sent them. Missing values stay null. Negative values are rejected. A quality index is computed only when loss, jitter and RTT are all present:

`index_v1 = clamp(0, 100, 100 - loss*2 - jitter_ms*0.5 - max(0, rtt_ms-150)*0.1)`

That index is not MOS. Provider MOS values are rejected.

## Isolation and failure

Every read filters on the authenticated tenant. A foreign id is a 404. Provider errors are typed. A handler does not catch an exception and return success. API responses do not include credentials or permanent recording URLs.
