# Opt-in real-provider integrations

`tests/real/` is intentionally separate from normal CI. Set
`VOXDESK_REAL_INTEGRATION=1` only in a credentialed environment that is approved
to make outbound calls, consume provider credits, write CRM sandbox records, and
send a real webhook. Every missing gate writes `SKIPPED_NO_CREDENTIALS`; a skip is
never converted into a provider pass.

The scheduled/manual workflow is `.github/workflows/real-integrations.yml`. It
uses the `real-provider-tests` GitHub Environment and the `REAL_TEST_*` secrets
mapped there. Keep those credentials limited to test accounts, a controlled test
callee, and CRM sandboxes. A scheduled run with no secrets is expected to fail the
fresh-real-evidence release gate after uploading its skip ledger. Manual dispatch
also has a `pstn_study_calls` input (default `0`); a positive value explicitly
requests that many additional paid PSTN calls and uploads dated JSON/CSV study
artifacts. The daily schedule never runs that extra N-call study.

## Provider gates

- **Twilio:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`,
  `TWILIO_TEST_TO_NUMBER`, `VOXDESK_PUBLIC_BASE_URL`, and
  `VOXDESK_REAL_API_KEY`. The test places one paid call through VoxDesk's
  authenticated `/api/calls` route. The caller ID must be configured for the
  test tenant; the destination must be a controlled, consented test handset/IVR
  that answers and speaks. The test requires a persisted caller and assistant
  transcript, a dual-channel carrier recording, and an authenticated replay.
  Recordings are deleted after assertions. Do not target an employee, customer,
  or unconsenting number.
- **Telnyx:** `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, `TELNYX_FROM_NUMBER`,
  `TELNYX_TO_NUMBER`, and `VOXDESK_PUBLIC_BASE_URL`. Use only a team-controlled,
  consented test destination; the Call Control test places one real call, looks
  it up, and asserts that the hangup action was accepted. The webhook URL is
  configured but webhook receipt is not asserted. Evidence is explicitly scoped
  to Call Control (`media_stream_requested: false`,
  `media_websocket_observed: false`); it does **not** prove that VoxDesk's signed
  media WebSocket or voice-agent pipeline received audio.

  Telnyx media-WebSocket proof remains outstanding. A future qualifying run must
  observe the application-owned socket with its valid single-use token bound to
  the persisted call, assert non-silent inbound audio reaches the voice pipeline
  and non-silent outbound audio is sent, and confirm call/stream cleanup. A
  successful `streaming_start` API response alone is not sufficient. The feature
  evidence generator fails closed: a generic Telnyx `PASS` cannot satisfy the
  `telnyx_bidirectional_media_audio` requirement without those observations.
- **SIP gateway PCAP:** `VOXDESK_SIP_GATEWAY_URL`,
  `VOXDESK_SIP_GATEWAY_TOKEN`, `VOXDESK_SIP_CAPTURE_TENANT_ID`, and
  `VOXDESK_SIP_CAPTURE_CALL_ID` must identify a controlled lab gateway and an
  already-active call carrying media. The test captures one second only; it
  does not originate or terminate the call, and it never writes packet bytes or
  call/tenant identifiers to the evidence ledger.
- **PCAP S3-compatible store:** requires a dedicated test bucket, region, test
  signing secret, and AWS credentials; an endpoint URL is optional for MinIO or
  another S3-compatible service. It uploads a valid empty PCAP under a random
  disposable key, verifies readback and the app HMAC, then deletes it. Cleanup
  is retried on failure; no bucket/key/credential or PCAP bytes enter evidence.
- **Webhook receiver:** `VOXDESK_REAL_WEBHOOK_RECEIVER_URL`,
  `VOXDESK_REAL_WEBHOOK_INSPECT_URL`, and `VOXDESK_REAL_WEBHOOK_SECRET`. Use a
  receiver owned by the team. The POST endpoint must verify HMAC-SHA256 over the
  exact request bytes and return JSON with `signature_verified: true` and the
  matching `event_id` only on success. The live test first posts a fresh,
  deliberately corrupted HMAC and requires a 4xx rejection; it then posts a valid
  signed event. The inspection endpoint accepts the `event_id` query and returns
  JSON containing `event_id`, `signature_verified`, a `headers` object (including
  `X-VoxDesk-Signature`, `X-VoxDesk-Timestamp`, and `X-VoxDesk-Event`), and `body`
  (the raw JSON text, or an equivalent decoded JSON object). The test checks both
  remote verdicts, the exact payload, and the echoed HMAC; a generic 2xx echo
  endpoint is not enough. A live PASS is valid only for the exact manifest test
  node; an unrun or skipped live test is not provider evidence.
- **STT/TTS/LLM, S2S, and CRM sandboxes:** each provider has its own credential
  gate in `tests/real/conftest.py`. Only test workspaces and disposable CRM rows
  are permitted. S2S input is resampled from the fixture WAV to the provider's
  required PCM16 rate before transmission.

## Local execution

Run from the repository root. This can create real side effects and consume paid
credits; use a dedicated test account and explicitly authorize the call first.

```bash
VOXDESK_REAL_INTEGRATION=1 \
VOXDESK_REAL_EVIDENCE_DIR="evidence/real-integrations/$(date -u +%F)" \
python -m pytest tests/real -m real_provider -q -rs \
  --junitxml="evidence/real-integrations/$(date -u +%F)/junit.xml"
```

The evidence writer stores provider outcomes in `real_integrations.json`. The
feature-matrix generator only grants `LIVE_PROVIDER` from a fresh (`<= 7` days),
non-faked ledger with qualifying `PASS` entries; skipped credentials are not
fresh evidence. No live provider or PSTN call was run to author this file.
