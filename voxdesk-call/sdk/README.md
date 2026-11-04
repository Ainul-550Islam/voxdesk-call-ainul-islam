# VoxDesk SDKs

Client libraries for the VoxDesk HTTP API. Every method in every SDK calls a
route that exists in `contracts/openapi.json`; nothing here is generated from a
plan or a mock. Responses are returned as the server sent them — a failure is an
error object, never an empty success.

| Language | Path | Transport | Status |
| --- | --- | --- | --- |
| Go | [`sdk/go`](go/README.md) | `net/http`, no external dependencies | implemented, `go vet` + `go test` in CI |
| Python | [`sdk/python/voxdesk`](python) | `httpx` | implemented |
| Node / TypeScript | [`sdk/node/src`](node) | `fetch` | implemented |
| React / React Native / Web | [`sdk/react`](react), [`sdk/react-native`](react-native), [`sdk/web`](web) | browser `fetch`, WebSocket | implemented |
| iOS / Android | [`sdk/ios`](ios), [`sdk/android`](android) | native HTTP clients | implemented |

`agent-softphone/` contains the embeddable softphone widget, not an API client.

## Common contract

All SDKs share the same five rules, because they are the ones the backend
actually enforces:

1. **Authentication** is `Authorization: Bearer <credential>`. The credential is
   either a user JWT from `POST /auth/login` or an API key created at
   `POST /api/identity/api-keys` — the server distinguishes them by prefix, so
   one header carries both.
2. **Tenant scope is never a client parameter.** The tenant is taken from the
   credential. A route that needs a tenant id in the path (for example
   `PATCH /api/tenants/{tenant_id}/profile`) validates that the id matches the
   caller's own hierarchy and returns 403 otherwise.
3. **Errors** arrive as `{"detail": {"code": ..., "message": ...}}` (sometimes as
   a plain string). SDKs surface the status, the code, the message and the
   `X-Request-ID` so a failure can be correlated with the server log.
4. **Idempotency**: `POST /api/calls` and the CSV import endpoints accept an
   `Idempotency-Key` header. Send one if you intend to retry; without it a
   create is not retried for you.
5. **Secrets are write-once.** API keys, SCIM tokens, webhook signing secrets and
   SSO client secrets are returned at creation/rotation and never by a read
   route. Store them before the call returns.

## Go

```go
client := voxdesk.New(voxdesk.Config{
    BaseURL: "https://api.example.com",
    Token:   os.Getenv("VOXDESK_TOKEN"),
})

agents, err := client.ListAgents(ctx, voxdesk.ListAgentsOptions{})
if err != nil {
    var apiErr *voxdesk.Error
    if errors.As(err, &apiErr) && apiErr.Status == http.StatusForbidden {
        log.Fatalf("missing permission: %s (request %s)", apiErr.Code, apiErr.RequestID)
    }
    log.Fatal(err)
}
```

See [`sdk/go/README.md`](go/README.md) for the full surface, retry semantics and
pagination helper.

## Testing

```bash
cd sdk/go && go vet ./... && go test ./...     # no network, no credentials
```

The Go suite runs against an in-process `httptest` server and asserts the wire
contract: paths, methods, headers, bodies, retry rules and error mapping.
