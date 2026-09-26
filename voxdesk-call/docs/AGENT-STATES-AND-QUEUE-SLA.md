# Agent states and queue SLA

Presence is a state, not a boolean. The states are `offline`, `available`,
`ringing`, `busy`, `wrap_up`, `away`, `paused` and `disabled`.

Legal moves:

- `offline` to `available` or, for a supervisor, `disabled`.
- `available` to `ringing`, `away`, `paused`, `offline`, or supervisor `disabled`.
- `ringing` to `busy`, `available` or `offline`.
- `busy` to `wrap_up` or `offline`.
- `wrap_up` to `available`, `offline` or `away`.
- `away` or `paused` to `available`, `offline`, or supervisor `disabled`.
- `disabled` only to `offline`, and only a supervisor may enter `disabled`.

The same state twice is a duplicate. A lost version is a conflict. Going
`offline`, `away`, `paused` or `disabled` releases that agent's active ACD
assignments and returns those items to waiting. A released or stale agent is
not offered new work.

A heartbeat updates `last_seen` only while the state is already live. It does
not move `offline` to `available`. An `available` agent whose `last_seen` is
older than 90 seconds is stale and ineligible. `wrap_up` older than 60
seconds with no active work can return to `available` on the presence sweep.

Adding someone to a queue does not make them available. Availability is an
explicit transition plus a fresh heartbeat timestamp, which the transition
to `available` sets.

## SLA

The SLA clock is the inbox clock. ACD starts it only when the inbox deadlines
are empty, and it does not overwrite a deadline that is already set. Breach
is read from `sla_state` when ordering a queue. Queue metrics are counts of
persisted waiting items, assigned items, and members whose presence is
`available` with a fresh heartbeat. Nothing in those counts is sampled or
invented.

## Supervisor

Force-state, release and reassign require `supervisor:write`. Each one is
authenticated, authorized, tenant-scoped, state-checked, audited, and
idempotent: repeating a release or a reassign to the same agent returns
`duplicate` and does not write a second owner.
