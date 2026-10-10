# PART 4 REPORT — Live Monitoring and Takeover (Gate G5)

## 1. Mission & Scope Summary
Executed `SELL_PROMPT_05_PART4_Live_Monitoring_Takeover.md` (**Gate G5**), replacing the row-only live monitoring facade (`F-03` `app/api/live_monitoring_routes.py::start_monitoring`) and unwired `LiveCallSession` table (`W-09`) with a real runtime audio/transcript fan-out, whisper-to-AI context injection, and supervisor takeover via provider TwiML replacement (`#31` Retell parity).

## 2. Files Created & Modified (14 Code Files + Contract Snapshots)
1. `app/telephony/monitor_bus.py` (`[NEW]`) — Per-call audio (`PCM16`) + transcript + whisper-to-AI guidance fan-out bus with bounded per-subscriber `asyncio.Queue`s, drop-oldest backpressure, Redis pub/sub cross-worker transport, session heartbeat/TTL tracking, and PII-free logging.
2. `app/agent/monitor_tap.py` (`[NEW]`) — Pipecat `FrameProcessor` (`MonitorTap` and companion `MonitorOutputTap`) placed in the live voice pipeline: tees caller/agent PCM16 audio and final transcripts to `monitor_bus` only while sessions/subscribers exist (zero overhead otherwise) and implements `inject_guidance(text)` (`LLMMessagesAppendFrame` + `LLMRunFrame`).
3. `app/agent/pipeline.py` (`[MODIFY]`) — Inserts `MonitorTap` and `MonitorOutputTap` into `_run_voice_agent` and checks `call.takeover_pending` in `handle_pipeline_disconnect` so a media-stream disconnect caused by a takeover TwiML replacement is never finalized as a caller hang-up.
4. `app/telephony/providers/base.py` (`[MODIFY]`) — Adds `supports_takeover: bool = False` and `bridge_to(external_id, destination, *, whisper_text=None, ...)` on `TelephonyProvider` and `TelephonyAdapter` (raising `UnsupportedCapability` on providers that do not yet implement takeover).
5. `app/telephony/providers/twilio.py` (`[MODIFY]`) — Sets `supports_takeover = True` on `TwilioAdapter` and implements `bridge_to(...)` via live call TwiML replacement (`<Dial>` / `<Conference>` with optional `<Say>` whisper and dual-channel recording continuity).
6. `app/telephony/takeover.py` (`[NEW]`) — Implements `Call.takeover_pending`, `takeover(...)`, `complete_takeover(...)`, `rollback_to_ai(...)`, `build_takeover_twiml(...)`, and `build_ai_rollback_twiml(...)`.
7. `app/api/ws/monitor_ws.py` (`[NEW]`) — Implements `WebSocket /ws/monitor/{call_id}` with short-lived HS256 JWT authentication (`issue_monitor_ws_token` / `verify_monitor_ws_token`, `calls.monitor` scope), tenant & session validation, max 5 supervisors per call, binary PCM16 + JSON transcript streaming, and clean close on `call_ended`.
8. `app/api/live_monitoring_routes.py` (`[MODIFY]`) — Enforces `calls.monitor` permission per tenant with audited `AuditAction.AUTHZ_DENIED` on 403, supports modes `listen | whisper_ai | takeover`, wires durable `RequestIdempotencyReceipt` (`1E`) and rate limits, manages durable `LiveCallSession` heartbeat/expiry, removes row-only `control_plane_only` stubs, and records canonical `live_monitor.*` governance audit events (never `AuditAction.RESOURCE_EXPORTED`).
9. `app/main.py` (`[MODIFY]`) — Mounts `monitor_ws_router` (`/ws/monitor/{call_id}`).
10. `dashboard-next/components/enterprise/live-call-card.tsx` (`[NEW]`) — Enterprise live call card displaying call status, live duration timer, sentiment badge, active supervisor count, and Listen / Whisper / Takeover controls.
11. `dashboard-next/app/dashboard/live/page.tsx` (`[NEW]`) — Supervisor live monitoring console with WebAudio PCM16 player, live transcript stream, whisper-to-AI guidance form, and Takeover confirmation modal requiring an audited supervisor note.
12. `tests/telephony/test_monitor_bus.py` (`[NEW]`) — 5 tests covering multi-subscriber fan-out, drop-oldest backpressure, zero frames when no listeners exist, max-supervisor cap, and `MonitorTap.inject_guidance`.
13. `tests/telephony/test_takeover.py` (`[NEW]`) — 4 tests covering `takeover_pending` preventing disconnect finalization, `<Dial>` TwiML replacement on `FakeTelephonyProvider`, AI stream rollback on failure, and `UnsupportedCapability` on Telnyx/Vonage.
14. `tests/api/test_live_monitoring_authz.py` (`[NEW]`) — 4 end-to-end async tests covering `calls.monitor` RBAC, cross-tenant isolation, WebSocket token expiry and PCM16/transcript streaming, durable idempotency, heartbeat/expiry, and `AuditLog` verification.

## 3. Acceptance Command Verification
- `make verify-truth` -> **61 passed** (`0` fake-success / filler / template-route violations)
- `python -c "from app.main import app; print('routes:', len(app.routes))"` -> `routes: 1158`
- `pytest -q tests/telephony/test_monitor_bus.py` -> **5 passed**
- `pytest -q tests/telephony/test_takeover.py` -> **4 passed**
- `pytest -q tests/api/test_live_monitoring_authz.py` -> **4 passed**
- `(cd dashboard-next && npx tsc --noEmit && npx vitest run)` -> **0 TypeScript errors, 6 test files / 81 tests passed**
