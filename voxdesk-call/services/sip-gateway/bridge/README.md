# SIP media bridge

The bridge carries one negotiated SIP media stream between RTP and the
VoxDesk WebSocket endpoint:

```text
SIP/RTP peer ── RTP/UDP ──► bridge ── WebSocket (G.711 binary + DTMF JSON) ──► app
```

The Go SIP gateway is the primary production entry point: it owns SIP
signalling, RTP sockets and its authenticated WebSocket client. The Python
`bridge/` package is the separate RTP/WebSocket bridge used by the SIP test
stack and by deployments that run a media relay independently. It does not
speak SIP. Do not start both media bridges for the same call.

## Wire protocol

The API endpoint at `/telephony/sip/ws` expects:

1. A bearer token plus a per-call HMAC session token in the HTTP upgrade
   headers. The token signs `call_id:unix_timestamp:nonce` with the shared
   `SIP_GATEWAY_TOKEN`.
2. A JSON text `start` frame containing `call_id`, `from`, `to`, `codec`, and
   `direction` (`inbound` or `outbound`).
3. Negotiated PCMU/PCMA G.711 binary media frames, unchanged. The API's
   serializer converts G.711 to pipeline PCM exactly once.
4. DTMF digits as JSON text frames. RTP telephone-event end retransmissions
   are deduplicated by RTP timestamp before one keypress is passed to the app.

## Modules

| Module | Responsibility |
|---|---|
| `codecs.py` | G.711 mu-law / A-law and PCM16 helpers for DSP tests |
| `rtp.py` | RTP v2 header, CSRC, extension and padding validation |
| `jitter.py` | Bounded sequence-order buffer, including 16-bit sequence rollover |
| `dtmf.py` | RFC 4733 telephone-event parsing and duplicate-end suppression |
| `session.py` | Per-call codec, payload type, jitter, DTMF and media statistics |
| `main.py` | One-call UDP RTP ↔ authenticated WebSocket process |

Run the deterministic media tests from the repository root:

```bash
python -m pytest tests/telephony/test_sip_bridge.py -q
```

## Running the standalone process

The image is built from `services/sip-gateway/Dockerfile.bridge`. Every call
needs its own RTP port and call identity. Required environment variables are
`BRIDGE_CALL_ID`, `BRIDGE_RTP_PORT`, `BRIDGE_APP_WS`, `BRIDGE_TOKEN`,
`BRIDGE_FROM_NUMBER`, and `BRIDGE_TO_NUMBER`. `BRIDGE_REMOTE_HOST` and
`BRIDGE_REMOTE_PORT` identify the RTP peer; the defaults are loopback and
5004. `BRIDGE_CODEC` accepts only `PCMU` or `PCMA`; `BRIDGE_DIRECTION` accepts
`inbound` or `outbound`.

```bash
BRIDGE_CALL_ID=demo \
BRIDGE_RTP_PORT=40000 \
BRIDGE_REMOTE_HOST=127.0.0.1 \
BRIDGE_REMOTE_PORT=5004 \
BRIDGE_APP_WS=ws://127.0.0.1:8000/telephony/sip/ws \
BRIDGE_TOKEN="$SIP_GATEWAY_TOKEN" \
BRIDGE_FROM_NUMBER=+15559998888 \
BRIDGE_TO_NUMBER=+15550001111 \
python -m bridge.main
```

The process uses a bounded queue and drops malformed/late media rather than
blocking the event loop. It returns `0` on normal close, `2` on invalid
configuration, and `3` when the app refuses or loses the media socket.

## Security and codec limits

* SRTP belongs at the SIP/RTP network edge; this bridge accepts plain RTP only
  on a private, controlled network. Do not expose the RTP port publicly.
* Only PCMU and PCMA are bridged. Opus and other codecs are rejected.
* No call recording or SIP signalling is performed by this process.
