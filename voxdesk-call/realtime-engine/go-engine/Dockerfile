# Go Realtime Engine — 10-25MB binary — 55K lines
FROM golang:1.21-bookworm as builder
WORKDIR /app
RUN apt-get update && apt-get install -y pkg-config && rm -rf /var/lib/apt/lists/*
COPY go.mod ./
RUN go mod download
COPY cmd/ cmd/
COPY internal/ internal/
COPY pkg/ pkg/
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o engine cmd/server/main.go
RUN ls -lh engine && strip engine || true && ls -lh engine

FROM debian:bookworm-slim as runtime
RUN apt-get update && apt-get install -y ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/engine /usr/local/bin/engine
COPY --from=builder /app/engine /app/engine
EXPOSE 8080 9090 8081
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD curl -f http://localhost:8081/health || exit 1
CMD ["engine", "--config", "config/default.toml"]
