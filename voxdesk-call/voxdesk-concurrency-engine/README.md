# VoxDesk Concurrency Engine — Real-time WebSockets & Concurrency Engine

**Size:** 10-25MB binary (release stripped)
**Lines:** 10K-20K+ lines Rust
**Purpose:** Real-time hundreds of voice calls, WebRTC/LiveKit signaling, high-speed data stream handling

## Architecture

- `config/` — default.toml, production.toml — WebSocket & Signaling configuration, SSL, Redis Cluster, Log levels
- `src/main.rs` — Entry point, CLI flags, server initialization
- `src/lib.rs` — Library entry exporting modules
- `src/config/` — Configuration management, env parsing, LiveKit credentials, WebSockets setup
- `src/core/` — Core Engine, Shared In-Memory State (Arc<RwLock<AppState>>, Active Sessions Map), Prometheus metrics, Fast Event Routing
- `src/signaling/` — WebRTC & LiveKit Signaling Protocol, LiveKit SDK, SDP parser/generator, ICE handler, Peer Connection manager
- `src/websocket/` — High-Concurrency WebSocket Gateway, Actix-web/Axum handshake, Session actor (Heartbeat, Ping/Pong, Write Buffers), Binary/JSON serialization, Global Client Manager (Broadcast, Room grouping)
- `src/audio/` — Low-Latency Audio Streaming Pipeline, Ring Buffer PCM/Opus sync, Frame packaging, Pipeline bridging LiveKit WebRTC audio to Python/AI Layer
- `src/redis/` — Distributed Pub/Sub & State Persistence, Async Redis Pool, Inter-node message bus, Ephemeral active call state sync
- `src/utils/` — Common utilities, HMAC-SHA256 Token verification, Tracing Subscriber JSON logs, Custom Error types
- `src/tests/` — Integration & Load Testing, End-to-end WS signaling, Tokio multi-client stress tester (1,000+ concurrent), Mock LiveKit

## Build

```bash
cargo build --release  # binary ~18MB stripped
./target/release/voxdesk-concurrency-engine --config config/default.toml
```

## Env Setup

```bash
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export REDIS_URL=redis://localhost:6379
export RUST_LOG=info
```

## Docker

```bash
docker build -t voxdesk/concurrency-engine:1.0.0 .
docker run -p 8080:8080 -p 9090:9090 -p 8081:8081 voxdesk/concurrency-engine:1.0.0
```
