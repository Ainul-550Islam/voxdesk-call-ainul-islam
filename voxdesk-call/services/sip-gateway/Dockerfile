# The VoxDesk SIP media gateway.
#
# A single static binary: the SIP user agent, the RTP sockets and the
# WebSocket bridge to the VoxDesk application. The build stage is pinned to
# the Go version the module needs; the runtime stage carries nothing but the
# binary and the CA certificates.

FROM golang:1.27-bookworm AS build

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY cmd ./cmd
COPY internal ./internal

RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/sip-gateway ./cmd/sip-gateway

FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=build /out/sip-gateway /usr/local/bin/sip-gateway

EXPOSE 5060/udp 5060/tcp 8080/tcp

USER nonroot:nonroot

# The health check is the binary itself: the distroless image has no shell,
# so the probe runs in the healthcheck mode of the same program.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/usr/local/bin/sip-gateway", "healthcheck", "http://127.0.0.1:8080/healthz"]

ENTRYPOINT ["/usr/local/bin/sip-gateway"]
