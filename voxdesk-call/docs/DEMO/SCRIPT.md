# VoxDesk — 10-Minute Buyer Due-Diligence Demo Script & Recording Manifest

This document provides the 10-minute technical walkthrough script for prospective
buyers and technical due-diligence reviewers, together with the manifest of the
**8 required real recordings and execution traces** captured in `evidence/demo/`
and bundled into `dist/due-diligence-<date>.zip`.

Every step in this script exercises only capabilities verified as `LIVE` in
[`docs/SALES/FEATURE_MATRIX_VERIFIED.md`](../SALES/FEATURE_MATRIX_VERIFIED.md).

---

## Part 1 — Required Real Recordings to Capture (8 Headline Features)

| # | Recording / Artifact | Matrix IDs | Evidence Files in `evidence/demo/` | What to Capture (Screen + Audio + Trace) |
|---|---|---|---|---|
| 1 | **Inbound PSTN Call & Voice Controls** | `#1`, `#2`, `#4`, `#6`, `#10` | `01_inbound_call.json`, `01_inbound_call_audio.wav` | Dial the Twilio number bound to a published `AgentVersion`; show `POST /telephony/voice` -> `WS /telephony/ws`, ambient background bed, interruption/barge-in, and persisted `CallLatencyStat` (`e2e_p50_ms`, `e2e_p95_ms`). |
| 2 | **Outbound Batch / Power Dialer** | `#17`, `#21` | `02_outbound_batch.json` | Upload a CSV batch (`POST /api/batch-calls`) containing valid E.164 numbers, a duplicate number, and a DNC-listed number; show DNC suppression, CPS token-bucket pacing, AMD voicemail detection (`leave_message` / `hangup`), and `SELECT ... FOR UPDATE SKIP LOCKED` single-claim proof. |
| 3 | **Browser Web Call & Embeddable Widget** | `#22`, `#37` | `03_web_call.json`, `03_web_call_audio.wav` | Open a page with `<script src="/widget/v1/embed.js" data-public-key="pk_live_..."></script>`; click **Start Call**, grant mic permission, speak two turns over `/api/v1/telephony/web-calls/{id}/ws`, and show origin-allowlist + single-use JWT enforcement. |
| 4 | **Live Supervisor Listen, Whisper & Takeover** | `#31` | `04_live_takeover.json` | While an inbound or web call is active, open the Supervisor Console (`calls.monitor` RBAC permission), click **Listen** (`/api/calls/{id}/monitor/ws`), send a **Whisper** instruction (`POST /api/calls/{id}/monitor/whisper`) that changes the AI's next response, then click **Takeover** (`POST /api/calls/{id}/monitor/takeover`) to bridge the caller to the human supervisor's phone/SIP URI. |
| 5 | **Visual Conversation-Flow Builder Call** | `#8`, `#9`, `#10` | `05_flow_call.json` | Open the React Flow visual builder (`dashboard` / `dashboard-next`), inspect a 6-node flow (`conversation` -> `function` -> `branch` -> `press_digit` -> `transfer` / `end`), click **Validate** (`0` errors) and **Publish**, bind the version to a phone number, and run a call that traverses the graph with real-time active-node highlighting. |
| 6 | **Signed Webhook Lifecycle Delivery & DLQ Redrive** | `#25`, `#38` | `06_webhook_delivery.json` | Show the webhook receiver receiving `call_started`, `call_ended`, and `call_analyzed` with `X-VoxDesk-Signature: t=<ts>,v1=<hmac_sha256>`, verify the signature via `voxdesk.webhooks.verify_signature`, simulate a 500 receiver outage to move a delivery to `dlq`, and redrive it via `POST /api/webhooks/deliveries/{id}/redrive`. |
| 7 | **Post-Call Analysis & Automated QA Review** | `#26`, `#27` | `07_post_call_analysis.json` | Inspect a completed call's automatically extracted `summary`, `sentiment`, `call_successful`, and typed `custom_analysis_data` (`boolean`, `number`, `enum`, `text`), alongside its automatic QA scorecard review (`QAReview`, per-criterion scores, transcript evidence citations, and `CoachingSignal`). |
| 8 | **Custom Analytics Dashboard & A/B Experiment** | `#28`, `#29`, `#30` | `08_custom_dashboard.json` | Show a live A/B experiment splitting inbound traffic (`50/50`) between Agent Version 1 and Version 2 with two-proportion z-test conversion metrics, then open a tenant-scoped custom analytics dashboard and export the CSV report (`GET /api/analytics/dashboards/{id}/export.csv`). |

---

## Part 2 — Minute-by-Minute 10-Minute Live Walkthrough Script

### Minute 0:00 – 1:00 | Stack Boot, Truth Gate & Multi-Tenant Architecture
1. Show the running stack (`docker compose ps` or `GET /health/ready` returning `200 OK` with `"database": {"ok": true}`, `"redis": {"ok": true}`).
2. Run `make verify-truth` (`0` filler findings, `0` fake-success markers, empty `scripts/fake_success_allowlist.txt`, `61 passed` truth tests).
3. Show [`docs/SALES/FEATURE_MATRIX_VERIFIED.md`](../SALES/FEATURE_MATRIX_VERIFIED.md) and point out that every `LIVE` row links to exact passing JUnit test nodes.

### Minute 1:00 – 2:30 | Agent Versioning, Phone Number Binding & Visual Flow Builder (`Recording #5`)
1. Open the Agent & Flow Builder UI.
2. Load the 6-node appointment-triage flow (`conversation` greeting -> `function` CRM lookup -> `branch` equation check -> `press_digit` extension -> `transfer` / `end`).
3. Click **Validate Flow** (`POST /api/flows/{id}/validate`) and **Publish Version** (`POST /api/agents/{id}/publish`), creating an immutable `AgentVersion` snapshot.
4. Bind the published version to `+15550001111` via `PATCH /api/v1/telephony/numbers/{number_id}`.

### Minute 2:30 – 4:00 | Live Inbound Call, Voice Controls & Turn-Taking (`Recording #1`)
1. Place a call to `+15550001111` (or run `python loadtest/voice_ws_user.py --self-test` for deterministic local replay).
2. Demonstrate:
   - Custom pronunciation dictionary and subtle `office.wav` ambient background sound bed (`-22 dBFS`).
   - Backchanneling (`"uh-huh"`) without cutting off the caller on a brief pause, followed by immediate barge-in interruption (<150 ms) when the caller speaks a full phrase.
3. Show the persisted `CallLatencyStat` row (`stt_ms`, `llm_ttft_ms`, `tts_ttfb_ms`, `e2e_p50_ms`, `e2e_p95_ms`).

### Minute 4:00 – 5:30 | Live Supervisor Listen, Whisper & Takeover (`Recording #4`)
1. During an active call, open the **Live Monitor** panel as a user with `calls.monitor` permission.
2. Subscribe to `/api/calls/{call_id}/monitor/ws` to hear the live mixed caller + AI audio stream.
3. Send a supervisor whisper (`POST /api/calls/{call_id}/monitor/whisper` with `{"instruction": "Offer the 15% new-patient discount now"}`) and observe the AI incorporate it on the next turn without reading the instruction aloud.
4. Trigger **Takeover** (`POST /api/calls/{call_id}/monitor/takeover`): show the Twilio REST call redirect (`calls(call_sid).update(twiml=...)`), PipelineTask cancellation, and `call.taken_over` audit/outbox event.

### Minute 5:30 – 6:45 | Browser Web Call & Embeddable Widget (`Recording #3`)
1. Open the sample customer landing page embedding `/widget/v1/embed.js` inside a Shadow DOM root.
2. Click the floating voice button; show `POST /api/v1/telephony/web-calls/public` validating the `Origin` header against the key's `allowed_origins` and minting a 60-second single-use JWT.
3. Speak with the agent in the browser, hang up, and show that replaying the same token returns `401 Unauthorized`.

### Minute 6:45 – 8:00 | Outbound Batch Dialer, DNC & AMD Voicemail (`Recording #2`)
1. Create a batch campaign (`POST /api/batch-calls`) with 5 recipients (including 1 duplicate and 1 DNC number).
2. Show immediate DNC filtering (`status="dnc_blocked"`), timezone window enforcement, and concurrent worker claims (`SELECT ... FOR UPDATE SKIP LOCKED`).
3. Show AMD voicemail detection (`machine_end_beep`) leaving the configured voicemail greeting and hanging up cleanly.

### Minute 8:00 – 9:00 | Webhooks, Post-Call Analysis, Auto-QA & Salesforce Writeback (`Recordings #6 & #7`)
1. Inspect the finished call in the Call Detail view:
   - Structured post-call extraction (`summary`, `sentiment`, `call_successful`, and custom schema fields).
   - Automatic QA review (`QAReview` + `QAReviewItem` scores + transcript quote citations).
2. Show the delivered `call_started`, `call_ended`, and `call_analyzed` webhooks with verified `X-VoxDesk-Signature` headers.
3. Show the encrypted Salesforce integration (`app/integrations/crm/providers/salesforce.py`) logging the completed call as a Salesforce `Task` with a real `external_id`.

### Minute 9:00 – 10:00 | Simulation, A/B Experiments, Custom Dashboards & Due-Diligence Pack (`Recording #8`)
1. Promote a failed call to a PII-redacted regression test case (`POST /api/simulations/from-call/{call_id}`) and run `SimulatedCaller` against the new `AgentVersion`.
2. Open the A/B Experiment view showing live call variant assignment and z-test significance, then open the Custom Dashboard and download the CSV export.
3. Conclude by showing `docs/CAPACITY_MODEL.md`, `evidence/dr/dr_drill_report.json` (`RPO = 0.35s`, `RTO = 2.23s`), and `dist/due-diligence-<date>.zip`.
