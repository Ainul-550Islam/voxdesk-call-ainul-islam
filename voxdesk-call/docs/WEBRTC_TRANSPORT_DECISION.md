# Web-call transport decision (PART 11, task P11-10)

**Status:** decided · **Date:** 2026-11-10 · **Owner:** platform

## Decision

**`WS /telephony/web/ws` (WebSocket + protobuf frames) is the only supported browser
transport for a live web call.** WebRTC is not offered to customers.

`POST /telephony/web/offer` exists, does real SDP/ICE/DTLS negotiation when `aiortc` is
installed, and is covered by a test that drives a genuine `aiortc` peer connection — but it
does **not** yet route the negotiated audio tracks into the agent pipeline, so a browser that
connects over it hears silence. Until that is wired and proven with audio, WebRTC stays off
the feature matrix and off the sales copy.

## What is actually true today

| Claim | Status | Proof |
| --- | --- | --- |
| The WebSocket protobuf transport carries a real web call (token → media → transcript) | **Yes** | `tests/telephony/test_web_call_flow.py`, `tests/telephony/test_no_mock_media_stream.py` |
| `/telephony/web/offer` returns `503 UNSUPPORTED_CAPABILITY` + `fallback_transport: "ws-protobuf"` when `aiortc` is absent | **Yes** | `tests/telephony/test_webrtc_dependency.py::test_webrtc_offer_returns_503_with_ws_protobuf_fallback_when_aiortc_absent` |
| A real `aiortc` browser offer is answered with a parsable SDP answer (ICE ufrag/pwd, DTLS fingerprint, `m=audio`, `a=setup`) and aiortc accepts that answer as a remote description | **Yes** | `tests/telephony/test_webrtc_real_negotiation.py::test_real_aiortc_offer_is_answered_with_a_parsable_webrtc_answer` (skips without `aiortc`) |
| Agent audio is sent to a WebRTC peer | **No** | the offer handler builds `SmallWebRTCConnection`, returns the answer and drops it; no pipeline task, no track sink |
| Customer-accepted WebRTC in production | **No** | never deployed; no browser E2E test, no audio assertion |

## Why not just ship WebRTC

1. **No media path.** The endpoint negotiates and returns; it never starts a bot pipeline on
   the connection, so the remote audio track is received and discarded. Shipping it now would
   be a silent failure mode, the exact class of defect this audit exists to remove.
2. **Codec/CPU cost.** `aiortc` pulls `av`/`pylibsrtp`/`pyopenssl` and does Opus encode plus
   SRTP in-process. On the measured 2 vCPU worker (`docs/CAPACITY_MODEL.md`) the denoise path
   already spends `~297 ms` CPU per 3-turn call; adding Opus+SRTP plus jitter buffers needs its
   own ramp before any concurrency claim.
3. **The WebSocket path already ships latency and interoperability** with the widget
   (`/widget/v1/embed.js`), including the same G.711 framing used on the PSTN leg, so there is
   one audio path to tune rather than two.
4. **TURN/SFU operations.** WebRTC in production means ICE servers, TURN credentials, certificate
   rotation and per-region UDP reachability. That is real infrastructure the buyer would inherit.

## What it would take to flip this decision

Tracked as **P11-10b** (not scheduled):

1. Run a pipecat `Pipeline` on the `SmallWebRTCConnection` created in
   `web_call_webrtc_offer_endpoint`, with a `SmallWebRTCTransport` as both input and output —
   the same `AgentSession` the WebSocket handler builds.
2. Persist/serve the connection for the lifetime of the call (registry keyed by `call_id`),
   with renegotiation and `on_connectionstatechange` cleanup, instead of dropping it.
3. Prove it: a headless browser (Playwright + Chromium) that joins, speaks a canned WAV, and
   asserts the transcript contains the expected tokens **and** that decoded agent audio arrives
   on the remote track. Until such a test is red-free, the matrix row stays `API_ONLY` for
   WebRTC.
4. Re-run the capacity ramp with WebRTC on (`docs/CAPACITY_MODEL.md` §4 mode 2) to publish a
   CPU/memory delta.
5. Only then add a WebRTC line to `docs/SALES/` copy, quoting the measured numbers.

## Reproduction

```bash
# The WebRTC-capable runtime (opt-in; not required to run VoxDesk)
pip install aiortc==1.16.0

# Real aiortc negotiation proves the signaling path
python -m pytest tests/telephony/test_webrtc_real_negotiation.py -q

# Without aiortc the fallback contract still holds
python -m pytest tests/telephony/test_webrtc_dependency.py -q
```

`requirements-test.txt` pins `aiortc==1.16.0`, so continuous integration exercises the
negotiation test for real instead of skipping it.
