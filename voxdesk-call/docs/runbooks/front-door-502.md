# Runbook — front door returns 502/504 while the API is healthy

## Symptom

`http://<front-door>/` answers, the marketing pages render, but anything under
`/api/…`, `/auth/…`, `/dashboard/…` fails with 502 or 504. Direct
`http://127.0.0.1:8000/health` returns `200`.

## Check first

```sh
curl -s -o /dev/null -w 'api=%{http_code}\n'   http://127.0.0.1:8000/health
curl -s -o /dev/null -w 'proxy=%{http_code}\n' http://127.0.0.1:8080/
curl -s http://127.0.0.1:8080/api/health | head -c 200
```

If `api=200` and `proxy=502`, the routing layer is the problem — not the app.

## Two causes seen here

### 1. The upstream died and the proxy cached its own error page

Symptoms in a test run: `SyntaxError: Unexpected token 'u', "upstream 1"...`.
That string is the proxy's error body. Restart the API (see
[api-oom.md](api-oom.md)); the proxy picks it up on the next request.

### 2. Duplicate `Content-Length` header

The dev proxy was appending its own `Content-Length` while forwarding the
upstream's. Chromium rejects that response outright, so every navigation failed
with a network error that looks like a product outage. Fixed in
`scripts/local_front_proxy.py` — the forwarded header is stripped
(`NOT_COPIED_FROM_UPSTREAM`) and recomputed once.

If you see this again after a proxy change:

```sh
curl -sI http://127.0.0.1:8080/ | grep -i content-length   # expect exactly one
```

## Production

The shipped topology is Caddy in front of `web:3000` and `api:8000`, with `@api`
path prefixes routed to the API. The equivalent checks:

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/api/health
docker logs --tail 50 voxdesk-api-1
```

Caddy logs the upstream error verbatim; read it before touching config. A 502
from Caddy usually means the container is not listening on the expected
interface — `hostNetwork` for the media engine and the bind address for the API
are the two that have actually bitten this deployment.

## Write down

- Which side of the front door failed (a 502 with a working API is a routing
  bug; a 000 is a dead process).
- Whether the failure was total or only on `@api` prefixes.
