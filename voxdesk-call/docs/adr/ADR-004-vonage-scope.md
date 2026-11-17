# ADR-004: Vonage scope

- **Status**: Accepted
- **Date**: 2026-11-10
- **Phase**: PART 13 — Telephony and carriers parity (Gate G13, P13-03)
- **Related**: ADR-001 (SIP ingress), `app/telephony/providers/vonage.py`

---

## Context

PART 13 asks for a second carrier proven live next to Telnyx, and names Vonage
as one of the candidates. Before writing code we have to answer a narrower
question than "does Vonage work": **which of our capabilities does Vonage
actually offer, and are we willing to claim them?**

Three facts shaped the decision:

1. **The Voice API is call-control, not media-stream-first.** Vonage answers
   calls with an NCCO (a JSON document of actions) and can stream audio, but the
   websocket leg is mono and 16-bit at either 8 kHz or 16 kHz with no
   bidirectional interrupt semantics matching Pipecat's expectations. Our
   barge-in behaviour depends on those semantics.
2. **Signing is JWT, not HMAC.** Vonage signs requests with a JWT built from an
   application ID and private key, and signs *webhooks* with a different JWT
   scheme. That is fine, but it is a second credential lifecycle to operate --
   key rotation, application IDs per environment -- and PART 16 already has to
   document key rotation once.
3. **We have no live account with a provisioned number.** Everything we could
   write today would be exercised only against mocks. The rule for this
   repository (PART 11 and the honesty rule in the audit) is that a capability
   flag is flipped only after the real test passes.

## Decision

Vonage ships as **a voice-only adapter with explicitly unsupported
capabilities**, not as a parity carrier.

* Implemented and covered by tests: JWT request signing, NCCO `connect` with a
  websocket endpoint, NCCO `record` (start/stop), NCCO `talk`, call control
  (`PUT /v1/calls/{uuid}` with `transfer`, `hangup`, `dtmf`), webhook signature
  verification, and the call status taxonomy mapping onto our
  `DisconnectionReason` values.
* Supported, and listed in `VONAGE_SUPPORTED_CAPABILITIES`: `voice`,
  `call_control`, `ncco`, `recording`, `dtmf`, `webhook_signature`.
* **Not** claimed, and listed with a reason in
  `VONAGE_UNSUPPORTED_CAPABILITIES`: `ai_media_stream` (credential-gated *and*
  without a recorded live run), `whatsapp` (a Messages API capability, not a
  Voice one), `number_provisioning`, `sms_registration`. `unsupported_capability(
  name)` is the single function that produces the refusal text, so the ADR and
  the message cannot drift apart.
* The capability row is generated from `vonage_media_capabilities()`, which now
  reports both lists, and `tests/telephony/test_vonage_scope.py` asserts them.
  A future change that quietly promotes Vonage fails a test rather than a
  customer.
* We make no Telnyx live-media claim. The real-provider Telnyx test only covers
  Call Control create/lookup and acceptance of a hangup action; it neither
  requests nor observes a media WebSocket and records
  `media_websocket_observed: false`. Authenticated media frames reaching the
  VoxDesk voice pipeline remain unverified and are explicitly gated as
  outstanding evidence.

## Consequences

* A tenant asking "do you support Vonage?" gets an accurate answer in the
  dashboard: yes for voice and call control, no for live AI media.
* No dead code: the unsupported branches are explicit, tested, and drive the UI.
* If a live Vonage account is provisioned later, flipping
  `supports_ai_media_stream` requires (a) `tests/real/test_vonage_live.py`
  passing with recorded evidence and (b) editing the capability row -- in that
  order. The test is the gate, not the marketing page.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Full parity implementation against mocks | It would be untested code claiming a capability we cannot demonstrate. Forbidden by the honesty rule. |
| Remove Vonage entirely | Existing tenants use it for voice; removing a working carrier is a regression, not a simplification. |
| Silent `capabilities=false` with no reason | An operator cannot tell "not yet implemented" from "your account is misconfigured". `UNSUPPORTED_CAPABILITY` names which. |
