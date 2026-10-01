# Rust Realtime Engine — 10-25MB binary — 52K lines
FROM rust:1.75-bookworm as builder
WORKDIR /app
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock* ./
COPY config/ config/
COPY src/ src/
RUN cargo build --release
RUN ls -lh target/release/ && strip target/release/voxdesk-realtime-engine || true && ls -lh target/release/voxdesk-realtime-engine

FROM debian:bookworm-slim as runtime
RUN apt-get update && apt-get install -y ca-certificates libssl3 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/target/release/voxdesk-realtime-engine /usr/local/bin/voxdesk-realtime-engine
COPY config/ config/
EXPOSE 8080 9090 8081
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD curl -f http://localhost:8081/health || exit 1
CMD ["voxdesk-realtime-engine", "--config", "config/default.toml"]
