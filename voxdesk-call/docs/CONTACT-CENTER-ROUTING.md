# Contact-center routing

ACD decides which queue item should be offered to which agent. It does not
own the conversation and it does not own the call.

- The inbox assignee (`InboxThreadState.assignee_id`) is the conversation
  owner. ACD writes it only through `app.inbox.assignment`.
- Telephony owns call state and the actual transfer. ACD calls
  `request_transfer` and records success only when `TransferResult.ok` is
  true. The dialed number is the tenant escalation number telephony already
  uses. ACD does not open a second transfer path.
- Routing does not run on the media stream and does not open a websocket.
  Selecting an agent must not block a live media socket.

## Policy

The choice is deterministic. It is not claimed to be optimal.

1. A disabled queue, a foreign user, a disabled user, a suspended or revoked
   or expired membership, a disabled queue member, a missing required skill,
   a disabled skill, a stale available heartbeat, or an agent at capacity is
   rejected. The reason is stored on the decision.
2. Remaining agents are ordered by the queue strategy. Ties break on user id.
   - `least_loaded`: fewest active assignments, then longest idle, then user id.
   - `longest_idle`: oldest `last_assigned_at` (never assigned wins), then user id.
   - `skill_first`: highest proficiency, then required-skill count, then member
     priority, then longest idle, then user id.
   - `priority_first`: member priority, then skill count, then proficiency, then user id.
   - `round_robin`: next user id after the stored cursor, wrapping at the end.
3. Queue items are ordered by an existing inbox SLA breach, then item
   priority, then wait age. Queue priority is the queue's own integer. There
   is no VIP flag and no inferred financial value.
4. An agent whose integer capacity is greater than 1 may receive another
   item while `active_count + 1` still fits. The increment is a compare-and-set
   on the presence version. Capacity 1 also takes a unique agent lock so a
   second active assignment cannot commit.
5. One active queue entry per call (`active_lock`) and one active assignment
   per entry (`entry_lock`). A lost version or a lock collision is a failure,
   not an assignment.

A missing billing subscription does not deny routing. An explicit
non-entitled subscription status does.

## Overflow

Overflow runs only when no eligible agent can take the item and the stored
policy says the wait or the waiting depth has been exceeded.

- `callback` is reported only after a `jobs` row with idempotency key
  `acd-callback:{entry_id}` exists. That row is a request. It is not a placed
  call.
- `escalate` is reported only when telephony accepts. A missing queue
  escalation number is `escalate_unavailable`. A telephony rejection is
  `escalate_failed`. Neither is an assignment.
- `voicemail` records the disposition `voicemail_requested`. It does not
  create a recording and it does not claim a message was stored.

## APIs

Tenant comes from the authenticated principal. A path or body `tenant_id`
that does not match is a 404. Cross-environment queue and call pairs are
rejected. Preview writes a decision with `applied=false` and does not assign.
