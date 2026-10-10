# PART 3 REPORT — Web Calls, Widget and SDKs (Gate G4 & Part of Gate G8)

## 1. Executive Summary
PART 3 (`SELL_PROMPT_04_PART3_Web_Calls_Widget_SDKs.md`) is complete and verified against all acceptance and truth-gate checks:
- **Facade F-05 Closed (`app/telephony/realtime.py` & `app/api/v1/telephony_routes.py`)**: Removed `RealtimeVoiceSessionOrchestrator`, `handle_ws_message`, and the `/calls/{call_id}/media-stream` + `/calls/{call_id}/media-event` + `/calls/{call_id}/media-token` mock routes that returned `"I heard: ..."` and `.encode("utf-8")` as fake audio. Preserved `ConfigurationError`, `issue_media_stream_token`, `verify_media_stream_token`, and `resolve_agent_runtime_profile`.
- **Facade F-06 Closed (`app/api/outbound_call_routes.py` & `app/telephony/web_call.py`)**: Removed the `tok_{uuid}_{call_id}` fallback token path and in-memory `_WEB_CALL_SESSIONS` store. All web-call creation now delegates to `app.telephony.web_call.create_web_call`, which fails closed when signing keys are unconfigured in production, enforces per-public-key and per-tenant rate limits, validates allowed origins, resolves `RuntimeConfig` via PART 2E (`resolve_runtime_config`), persists `Call(direction="web")`, and issues a short-lived single-use HS256 JWT (`access_token`) bound to `call_id`, `tenant_id`, `jti`, and `origin` with durable replay protection via `RequestIdempotencyReceipt`.
- **Live Browser Audio Transport (`app/telephony/web_transport.py`)**:
  - `WS /telephony/web/ws`: Uses Pipecat `FastAPIWebsocketTransport` + `ProtobufFrameSerializer` (16 kHz mono PCM16) wired to `LatencyObserver`, `TurnTrackingObserver`, `UsageTracker`, `GovernedHearing`, `GovernedSpeech`, `Turn` persistence, `CallLatencyStat` persistence, `media_gateway_manager`, and billing usage hooks (`on_llm_tokens`, `on_tts_characters`).
  - `POST /telephony/web/offer`: SmallWebRTC SDP signaling endpoint that validates the single-use JWT and returns honest `501 UNSUPPORTED_CAPABILITY` (`fallback_transport: "ws-protobuf"`) when `aiortc` is not installed.
- **Live Web-Call Routes & Public Widget Bootstrap (`app/api/web_call_live_routes.py`, `app/services/public_widget_service.py`, `app/api/public_widget_routes.py`, `widget/embed.js`)**:
  - Mounted `POST /api/web-calls`, `POST /api/public/web-calls`, `GET /api/web-calls/{call_id}`, `POST /api/web-calls/{call_id}/end`, `GET /widget/embed.js`, and `GET /api/v1/public/widget/embed.js`.
  - Preserved `/api/v1/testing/web-calls` (`app/api/web_call_routes.py`) with `tags=["testing-web-calls"]`.
- **SDKs, Dashboard Test-Call Page, and Connectors**:
  - `@voxdesk/web-sdk` (`sdk/web/`): `VoxDeskWebClient`, AudioWorklet 16 kHz mono PCM16 capture + jitter-safe playback queue, Pipecat `frames.proto` Protobuf codec, SmallWebRTC signaling client, and Vitest test suite (`3 passed`).
  - `@voxdesk/react` (`sdk/react/`): `useVoxDeskCall` hook + `<VoxDeskCallButton />`.
  - `@voxdesk/node` (`sdk/node/`): Typed `fetch` client on the OpenAPI contract + `verifyWebhookSignature()` + pagination helpers.
  - Python SDK (`sdk/python/voxdesk/client.py` + `sdk/client.py` compatibility shim): Sync (`SyncVoxDeskClient`) + async (`VoxDeskClient`) clients with Pydantic models, retries, and `verify_webhook_signature()`.
  - Dashboard test-call page (`dashboard-next/app/dashboard/agents/[id]/test-call/page.tsx`): Browser voice call panel with mic permission state, live transcript, DTMF keypad, mute/unmute, and latency badge.
  - `integrations/n8n/` & `integrations/zapier/`: Webhook trigger (`POST /api/webhooks`, `DELETE /api/webhooks/{id}`) and action nodes (`createCall`, `createWebCall`, `createBatch`, `getCall`).

---

## 2. Files Created / Modified / Verified in PART 3 (37 Files)

| # | Path | Status | Summary |
|---|---|---|---|
| 1 | `app/telephony/web_call.py` | `[NEW]` | `create_web_call`, `create_public_web_call`, single-use HS256 JWT, replay protection, origin allowlist, rate limit, `Call(direction="web")` |
| 2 | `app/telephony/web_transport.py` | `[NEW]` | `/telephony/web/ws` (`FastAPIWebsocketTransport` + `ProtobufFrameSerializer`) & `/telephony/web/offer` (`SmallWebRTCTransport` / 501 fallback) |
| 3 | `app/api/web_call_live_routes.py` | `[NEW]` | `POST /api/web-calls`, `POST /api/public/web-calls`, `GET /api/web-calls/{id}`, `POST /api/web-calls/{id}/end`, `GET /widget/embed.js` |
| 4 | `app/api/v1/telephony_routes.py` | `[MODIFY]` | Deleted mock `/calls/{call_id}/media-stream`, `/media-event`, and `/media-token` routes (F-05) |
| 5 | `app/telephony/realtime.py` | `[MODIFY]` | Deleted `RealtimeVoiceSessionOrchestrator` and `handle_ws_message`; kept token & profile helpers |
| 6 | `app/telephony/media_gateway.py` | `[VERIFY]` | Wired `media_gateway_manager` into `app/telephony/web_transport.py` |
| 7 | `app/api/outbound_call_routes.py` | `[MODIFY]` | Deleted `tok_{uuid}` fallback token path; delegated web calls to `app.telephony.web_call` (F-06) |
| 8 | `app/services/public_key_service.py` | `[VERIFY]` | Verified `PublicWidgetKey` scoping to agent, allowed origins, capabilities, and rate limits |
| 9 | `app/services/public_widget_service.py` | `[MODIFY]` | Real web-call bootstrap when voice transport is configured; `NOT_CONFIGURED` otherwise |
| 10 | `app/api/public_widget_routes.py` | `[MODIFY]` | Updated `voice-bootstrap` and added `GET /api/v1/public/widget/embed.js` |
| 11 | `app/api/web_call_routes.py` | `[KEEP]` | Preserved `/api/v1/testing/web-calls` text-based test-session API (`tags=["testing-web-calls"]`) |
| 12 | `app/main.py` | `[MODIFY]` | Mounted `web_call_live_router` alongside `web_call_router` |
| 13 | `widget/embed.js` | `[NEW]` | CSP-safe one-line embeddable widget (`data-public-key`, `data-agent-id`, floating button, call UI, Protobuf audio) |
| 14 | `sdk/client.py` | `[MODIFY]` | Backwards-compatible shim re-exporting `sdk/python/voxdesk/client.py` |
| 15 | `sdk/python/voxdesk/__init__.py` | `[NEW]` | Package exports for `sdk.python.voxdesk` |
| 16 | `sdk/python/voxdesk/client.py` | `[NEW]` | Sync + async Python SDK with Pydantic models, retries, web calls, batch calls, webhooks, and `verify_webhook_signature` |
| 17 | `sdk/web/package.json` | `[NEW]` | `@voxdesk/web-sdk` package manifest |
| 18 | `sdk/web/tsconfig.json` | `[NEW]` | TypeScript configuration for `@voxdesk/web-sdk` |
| 19 | `sdk/web/src/audio/worklet-capture.ts` | `[NEW]` | 16 kHz mono PCM16 AudioWorklet capture, constraints, resampler, and `JitterSafePlaybackQueue` |
| 20 | `sdk/web/src/transport/ws-protobuf.ts` | `[NEW]` | Pipecat `frames.proto` descriptor + Protobuf wire encoder/decoder + `WsProtobufTransport` |
| 21 | `sdk/web/src/transport/webrtc.ts` | `[NEW]` | `SmallWebRTCClient` signaling client for `/telephony/web/offer` |
| 22 | `sdk/web/src/index.ts` | `[NEW]` | `VoxDeskWebClient` (`startCall`, `stopCall`, `mute`, `unmute`, `sendDtmf`, event emitter) |
| 23 | `sdk/web/tests/client.test.ts` | `[NEW]` | Vitest unit test suite for `@voxdesk/web-sdk` |
| 24 | `sdk/react/package.json` | `[NEW]` | `@voxdesk/react` package manifest |
| 25 | `sdk/react/src/index.tsx` | `[NEW]` | `useVoxDeskCall` hook and `<VoxDeskCallButton />` component |
| 26 | `sdk/node/package.json` | `[NEW]` | `@voxdesk/node` package manifest |
| 27 | `sdk/node/src/index.ts` | `[NEW]` | `VoxDeskNodeClient`, `verifyWebhookSignature`, `paginatePages`, `collectAllPages` |
| 28 | `dashboard-next/app/dashboard/agents/[id]/test-call/page.tsx` | `[NEW]` | "Test your agent" browser call panel using `@voxdesk/web-sdk` |
| 29 | `integrations/n8n/package.json` | `[NEW]` | `n8n-nodes-voxdesk` community node package manifest |
| 30 | `integrations/n8n/credentials/VoxDeskApi.credentials.ts` | `[NEW]` | n8n credential type for VoxDesk API key & webhook signing secret |
| 31 | `integrations/n8n/nodes/VoxDesk/VoxDesk.node.ts` | `[NEW]` | n8n Action node (`createCall`, `createWebCall`, `createBatch`, `getCall`) |
| 32 | `integrations/n8n/nodes/VoxDesk/VoxDeskTrigger.node.ts` | `[NEW]` | n8n Trigger node subscribing/unsubscribing via `/api/webhooks` |
| 33 | `integrations/zapier/package.json` | `[NEW]` | Zapier platform app manifest |
| 34 | `integrations/zapier/index.js` | `[NEW]` | Zapier triggers and actions on `/api/webhooks`, `/api/web-calls`, `/api/v1/telephony/calls`, `/api/calls/outbound/bulk`, `/api/calls/{id}` |
| 35 | `tests/telephony/test_no_mock_media_stream.py` | `[NEW]` | Verifies F-05 and F-06 removal and `media_gateway` wiring |
| 36 | `tests/telephony/test_web_call_flow.py` | `[NEW]` | End-to-end web call creation, Protobuf WS audio/DTMF/transcript/latency flow, `UsageEvent` & `CallLatencyStat` persistence, SmallWebRTC 501, widget & Python SDK |
| 37 | `tests/telephony/test_web_call_security.py` | `[NEW]` | Single-use JWT replay rejection, Origin allowlist enforcement, expired JWT rejection, per-public-key rate limiting, and fail-closed production secret check |

---

## 3. Verification Output

- `pytest -q tests/telephony/test_no_mock_media_stream.py tests/telephony/test_web_call_flow.py tests/telephony/test_web_call_security.py` -> **12 passed**
- `cd sdk/web && npm test && npm run typecheck` -> **1 test file passed (3 tests), 0 TypeScript errors**
- `cd dashboard-next && npx tsc --noEmit` -> **0 TypeScript errors**
- `python -c "from sdk.client import VoxDeskClient; from sdk.python.voxdesk.client import VoxDeskClient as C2; assert VoxDeskClient is C2"` -> **PASSED**
- `make verify-truth` -> **61 passed** (`strip_generated_tails`, `verify_no_filler`, `verify_no_fake_success`, `verify_retired_references`, `verify_no_null_bytes`, `strip_padding_markers`, `tests/truth`)
