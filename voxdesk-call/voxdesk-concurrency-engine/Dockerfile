# Multi-stage Docker build for minimal Alpine/Scratch deployment — 10-25MB binary
FROM rust:1.75-bookworm as builder
WORKDIR /app
RUN apt-get update && apt-get install -y pkg-config libssl-dev libopus-dev clang && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock ./
COPY config/ config/
COPY src/ src/
RUN cargo build --release --locked
RUN strip target/release/voxdesk-concurrency-engine
RUN ls -lh target/release/voxdesk-concurrency-engine

FROM debian:bookworm-slim as runtime
RUN apt-get update && apt-get install -y ca-certificates libssl3 libopus0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/target/release/voxdesk-concurrency-engine /usr/local/bin/voxdesk-concurrency-engine
COPY config/ config/
EXPOSE 8080 9090 8081
ENV RUST_LOG=info
ENV APP_ENV=production
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD curl -f http://localhost:8081/health || exit 1
CMD ["voxdesk-concurrency-engine", "--config", "config/production.toml"]

FROM scratch as minimal
COPY --from=builder /app/target/release/voxdesk-concurrency-engine /voxdesk-concurrency-engine
COPY --from=runtime /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
EXPOSE 8080 9090 8081
ENTRYPOINT ["/voxdesk-concurrency-engine"]
