# `@voxdesk/cli`

Agent, call, workflow and knowledge operations from a terminal.

```bash
export VOXDESK_API_KEY=vd_...
export VOXDESK_BASE_URL=https://your-voxdesk-host

voxdesk agents list
voxdesk agents get agent_123
voxdesk calls get call_456
voxdesk workflows catalog
voxdesk knowledge search "refund policy"
```

`--json` prints machine-readable output and nothing else, so it can be piped into
`jq`; every non-2xx response exits `1` with the server's error code, and a missing
API key exits `2` with one line (no stack trace).

## Tests

```bash
cd sdk/cli && npm test    # node --test, 9 tests, no network
```

The command layer is a pure function — `run(argv, {fetch, out, err, env})` — so
the suite drives it with a stub transport and asserts on the requests it makes.

## Evidence tier

`LOCAL` — the suite runs here against a stub. No live API call has been recorded.
