# VoxDesk Go SDK

A small, dependency-free client for the VoxDesk HTTP API. Standard library only —
`go.mod` has no `require` block, so adding the SDK to a service adds nothing to
its dependency graph.

```bash
go get github.com/Ainul-550Islam/voxdesk-call-ainul-islam/sdk/go
```

```go
import voxdesk "github.com/Ainul-550Islam/voxdesk-call-ainul-islam/sdk/go"
```

## Quick start

```go
client := voxdesk.New(voxdesk.Config{
    BaseURL: "https://api.example.com",   // no trailing slash, no /api prefix
    Token:   os.Getenv("VOXDESK_TOKEN"),  // user JWT or API key
})

agent, err := client.CreateAgent(ctx, voxdesk.CreateAgentRequest{Name: "Night shift"})
if err != nil {
    return err
}

call, err := client.CreateCall(ctx, voxdesk.CreateCallRequest{
    To:             "+15550001111",
    AgentID:        &agent.AgentID,
    IdempotencyKey: ptr("order-4471-call-1"),
})
```

`Config.BaseURL` defaults to `http://localhost:8000`, which is the address
`make dev` serves the API on.

## What is covered

| File | Routes |
| --- | --- |
| `agents.go` | `GET /api/v1/agents`, `POST /api/v1/agents`, `GET /api/v1/agents/{id}`, `DELETE /api/v1/agents/{id}`, `GET /api/v1/agents/{id}/versions`, `POST /api/v1/agents/{id}/publish` |
| `calls.go` | `GET /api/calls`, `POST /api/calls`, `GET /api/calls/{id}`, `POST /api/calls/{id}/end`, `GET /api/calls/{id}/transcript` |
| `webhooks.go` | endpoint CRUD, enable/disable, deliveries, delivery retry, DLQ replay, secret rotation, test event, outbox health |
| `client.go` | transport, auth, retries, pagination, error mapping |

The field names on every struct are transcribed from `contracts/openapi.json`
(`AgentBuilderOut`, `AgentVersionOut`, `OutboundCallOut`, `WebhookCreate`, …).
Where the contract declares an untyped response — `GET /api/calls/{id}` and the
delivery-log routes — the SDK returns `map[string]any` instead of a struct that
would pretend to know provider-specific fields.

## Behaviour you can rely on

**Retries.** GET, PUT, PATCH and DELETE are retried up to twice on `429`, `502`,
`503` and `504`, honouring `Retry-After`. A `POST` is retried **only** when an
idempotency key was supplied, because repeating a dial or a create is a real
side effect rather than a harmless read.

**Errors.** Every non-2xx response becomes `*voxdesk.Error` carrying `Status`,
`Code` (the server's machine-readable code), `Message`, `RequestID` and the raw
`Body`:

```go
if errors.As(err, &apiErr) {
    switch {
    case voxdesk.IsUnauthorized(err): // 401 — refresh the credential
    case voxdesk.IsForbidden(err):    // 403 — the role lacks the permission
    case voxdesk.IsNotFound(err):     // 404
    }
}
```

**Request IDs.** Each attempt sends an `X-Request-ID`; the server echoes one
back and the SDK prefers the server value, so an error string always contains the
id that appears in the API log.

**Pagination.** List routes that page return `{total, limit, offset}`. Use the
route-specific helper to walk everything:

```go
calls, err := client.AllCalls(ctx, voxdesk.ListCallsOptions{Status: "completed"}, 100)
```

`agents.go`'s `ListAgents` deliberately does **not** paginate: `GET /api/v1/agents`
returns a bare array.

## Context and timeouts

Every method takes a `context.Context` as its first argument and passes it to
`http.NewRequestWithContext`, so cancellation and deadlines propagate. The
default `http.Client` has a 30-second timeout; supply `Config.HTTPClient` to
change it, add a proxy, or instrument calls.

## Testing

```bash
go vet ./...
go test ./...
```

`client_test.go` runs the client against an in-process `httptest` server. It
asserts the wire contract rather than mocking the SDK: paths, query encoding,
identifier escaping, headers (`Authorization`, `X-Request-ID`,
`Idempotency-Key`), request bodies, the retry rules above, and error decoding for
both structured (`{"detail": {"code": ...}}`) and string (`{"detail": "Not found"}`)
error bodies. No network access and no credentials are required.
