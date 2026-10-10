# ADR-001: BYOC SIP Trunking & SIP-to-WebSocket Media Bridge Ingress

- **Status**: Accepted
- **Date**: 2026-10-08
- **Phase**: PART 2 — Sub-Phase 2F (Voice Runtime Parity, Gate G3)

---

## Context

Enterprise customers using Bring-Your-Own-Carrier (BYOC) SIP trunks (Cisco Unified Border Element, Avaya Session Manager, Twilio Elastic SIP Trunking, Telnyx FQDN SIP, Genesys Cloud) require direct SIP signaling (`UDP/5060`, `TCP/5060`, `TLS/5061`) and RTP/SRTP media termination into VoxDesk's Pipecat voice pipeline without forcing PSTN hairpinning through a proprietary CPaaS TwiML layer.

Python's asyncio runtime is well-suited for WebSocket frame processing (`SIPBridgeFrameSerializer`) and control-plane authentication (`SipConnectionService`), but terminating raw kernel-level UDP RTP/SRTP streams with jitter buffering and RFC 4733 DTMF extraction directly inside the Python GIL introduces unacceptable packet-loss and p99 latency jitter under load.

---

## Decision

We adopt a two-tier **Kamailio + RTPEngine SIP-to-WebSocket Media Bridge** (`services/sip-gateway/`) sitting in front of the VoxDesk FastAPI/Pipecat runtime:

1. **Signaling Tier (`Kamailio 5.8`)**:
   - Terminates SIP `INVITE`, `ACK`, `BYE`, `CANCEL`, and `OPTIONS` health probes over UDP/TCP (`5060`) and TLS (`5061`).
   - Calls VoxDesk's internal SIP routing & authentication hook (`SipConnectionService.route_inbound_sip_invite`) to enforce:
     - **CIDR IP ACLs** (`check_sip_ip_acl`),
     - **RFC 2617 MD5 Digest Authentication** (`verify_sip_digest_auth`), and
     - **E.164 Number-to-Agent Resolution** (`resolve_runtime_config`).
2. **Media Tier (`ngcp-rtpengine`)**:
   - Terminates carrier RTP / SRTP (`PCMU`, `PCMA`, `G.722`, `Opus`) in kernel space and bridges the bidirectional audio stream over a local WebSocket (`wss://voxdesk-api:8000/telephony/sip/stream/{call_id}`) using JSON/PCMU frames consumed by `SIPBridgeFrameSerializer` (`app/telephony/media/serializers.py`).

---

## Consequences

- **Pros**:
  - Zero-GIL RTP packet pacing and SRTP decryption via kernel-mode `rtpengine`.
  - Unified Pipecat pipeline: SIP calls use the exact same `LatencyObserver`, `RuntimeConfig`, `FailoverServiceWrapper`, and `SIPBridgeFrameSerializer` abstraction as Twilio and Telnyx calls.
  - Strict tenant isolation via per-trunk CIDR ACLs and Digest credential hashes (`sec_ref_sha256_*`).
- **Cons**:
  - Requires deploying the companion `services/sip-gateway/docker-compose.sip.yml` stack in environments that terminate raw SIP/RTP directly.
