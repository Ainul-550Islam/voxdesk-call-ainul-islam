#!/usr/bin/env bash
# Apply VoxDesk Batch 04 to a checkout that already contains Batches 01-03.
set -Eeuo pipefail

PATCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${1:-${VOXDESK_REPO:-}}"
if [[ -z "$REPO_DIR" ]]; then
  echo "Usage: bash apply_batch04.sh /path/to/voxdesk-call" >&2
  exit 2
fi
REPO_DIR="$(cd "$REPO_DIR" && pwd)"
trap 'echo "BATCH04 failed at line $LINENO; no success is inferred from partial output." >&2' ERR

for required in app/main.py app/auth/permissions.py app/auth/rbac.py alembic/env.py scripts/scheduler.py Makefile; do
  if [[ ! -f "$REPO_DIR/$required" ]]; then
    echo "BATCH04: not a VoxDesk repository root: missing $required" >&2
    exit 3
  fi
done
if [[ ! -f "$REPO_DIR/alembic/versions/0065_compliance_v2.py" ]]; then
  echo "BATCH04: migration 0065_compliance_v2 is required. Apply Batches 01-03 first; no files were changed." >&2
  exit 4
fi
if ! grep -q 'down_revision = "0065_compliance_v2"' "$PATCH_DIR/alembic/versions/0066_contact_center_v2.py"; then
  echo "BATCH04: the patch migration chain is invalid." >&2
  exit 5
fi
if ! current_output="$(cd "$REPO_DIR" && python3 -m alembic current 2>&1)"; then
  printf '%s\n' "$current_output" >&2
  echo "BATCH04: could not read the migration precondition; no files were changed." >&2
  exit 7
fi
current_count="$(printf '%s\n' "$current_output" | awk 'match($0, /[0-9]{4}_[A-Za-z0-9_]+/) { print substr($0, RSTART, RLENGTH) }' | sort -u | wc -l | tr -d ' ')"
current="$(printf '%s\n' "$current_output" | awk 'match($0, /[0-9]{4}_[A-Za-z0-9_]+/) { print substr($0, RSTART, RLENGTH) }' | sort -u | head -n 1)"
if [[ "$current_count" != "1" ]] || { [[ "$current" != "0065_compliance_v2" ]] && [[ "$current" != "0066_contact_center_v2" ]]; }; then
  echo "BATCH04: database is at '${current:-uninitialized}' (revision count $current_count), not exactly 0065_compliance_v2 or an already-applied 0066_contact_center_v2; upgrade through Batch 03 first. No files were changed." >&2
  exit 7
fi

# Every shared edit is anchor-checked before any shared file is written. A
# complete marker pair makes a rerun a no-op; a half marker is always fatal.
python3 - "$REPO_DIR" <<'PY'
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

root = Path(sys.argv[1])

@dataclass(frozen=True)
class Edit:
    path: str
    name: str
    anchor: str
    replacement: str
    mode: str = "replace"


edits: list[Edit] = []

def edit(path: str, name: str, anchor: str, replacement: str, mode: str = "replace") -> None:
    edits.append(Edit(path, name, anchor, replacement, mode))


def block(indent: str, name: str, body: str) -> str:
    # Keep the original BEGIN/END anchors for Batch 05 compatibility while
    # also emitting the exact parallel-safety marker names required here.
    # BATCH04:contact_center_v2_routes
    # BATCH04:contact_center_v2_jobs
    # BATCH04:contact_center_v2_models
    # BATCH04:permissions
    contract_markers = {
        "permissions": "permissions",
        "model-registration": "contact_center_v2_models",
        "route-imports": "contact_center_v2_routes",
        "job-handlers": "contact_center_v2_jobs",
    }
    contract = contract_markers.get(name)
    marker = f"{indent}# BATCH04:{contract}\n" if contract else ""
    return (
        f"{indent}# BATCH04:BEGIN {name}\n"
        + marker
        + body.rstrip("\n")
        + f"\n{indent}# BATCH04:END {name}\n"
    )


# Permission vocabulary and existing role grants.
edit(
    "app/auth/permissions.py",
    "permissions",
    '    SUPERVISOR_WRITE = "supervisor:write"\n',
    '''    SUPERVISOR_WRITE = "supervisor:write"
    DIALER_READ = "dialer:read"
    DIALER_WRITE = "dialer:write"
    DIALER_OPERATE = "dialer:operate"
    SOFTPHONE_USE = "softphone:use"
    ASSIST_READ = "assist:read"
    ASSIST_WRITE = "assist:write"
''',
)
edit(
    "app/auth/rbac.py",
    "agent-grants",
    '''        Permission.QUEUE_READ,
        Permission.AGENT_STATE_READ,
        Permission.AGENT_STATE_WRITE,
''',
    '''        Permission.QUEUE_READ,
        Permission.AGENT_STATE_READ,
        Permission.AGENT_STATE_WRITE,
        Permission.DIALER_READ,
        Permission.DIALER_OPERATE,
        Permission.SOFTPHONE_USE,
        Permission.ASSIST_READ,
        Permission.ASSIST_WRITE,
''',
)
edit(
    "app/auth/rbac.py",
    "manager-grants",
    "    Permission.SUPERVISOR_READ,\n",
    '''    Permission.SUPERVISOR_READ,
    Permission.DIALER_READ,
    Permission.DIALER_WRITE,
    Permission.DIALER_OPERATE,
    Permission.SOFTPHONE_USE,
    Permission.ASSIST_READ,
    Permission.ASSIST_WRITE,
''',
)

# Model registration is deterministic in app startup, tests, and Alembic.
for path, anchor in (
    ("alembic/env.py", "import app.db.enterprise_models  # noqa: F401\n"),
    ("tests/conftest.py", "import app.db.enterprise_models  # noqa: F401\n"),
):
    edit(
        path,
        "model-registration",
        anchor,
        anchor + "import app.db.contact_center_v2_models  # noqa: F401\n",
    )
edit(
    "app/main.py",
    "model-registration",
    "from app.db.session import get_engine\n",
    "import app.db.contact_center_v2_models as contact_center_v2_models  # noqa: F401\nfrom app.db.session import get_engine\n",
)

# CC2 credentials die inside the canonical identity revocation transaction.
edit(
    "app/auth/identity/sessions.py",
    "identity-softphone-revocation-helper",
    "async def revoke_session(\n",
    '''async def _revoke_cc2_softphone(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    from app.contact_center_v2.softphone.token_service import revoke_softphone_sessions

    await revoke_softphone_sessions(session, tenant_id=tenant_id, user_id=user_id)


''',
    mode="before",
)
edit(
    "app/auth/identity/sessions.py",
    "identity-softphone-revoke-one",
    "    tokens = await _revoke_tokens_for_session(session, row.id, reason=reason)\n",
    '''    tokens = await _revoke_tokens_for_session(session, row.id, reason=reason)
    await _revoke_cc2_softphone(session, tenant_id=row.tenant_id, user_id=row.user_id)
''',
)
edit(
    "app/auth/identity/sessions.py",
    "identity-softphone-revoke-many",
    "        await _revoke_tokens_for_session(session, row.id, reason=reason)\n",
    '''        await _revoke_tokens_for_session(session, row.id, reason=reason)
    for tenant_id, user_id in {(row.tenant_id, row.user_id) for row in rows}:
        await _revoke_cc2_softphone(session, tenant_id=tenant_id, user_id=user_id)
''',
)
edit(
    "app/auth/identity/sessions.py",
    "identity-softphone-revoke-empty",
    "    return await revoke_sessions(session, rows, reason=reason, commit=commit)\n\n\nasync def sweep_expired",
    '''    if not rows:
        user = await session.get(User, user_id)
        if user is not None:
            await _revoke_cc2_softphone(session, tenant_id=user.tenant_id, user_id=user.id)
''',
    mode="before",
)

# Concrete CC2 routers are mounted before the SPA fallback and before any
# dynamic route that could otherwise capture a concrete path.
edit(
    "app/main.py",
    "route-imports",
    "from app.channels.messaging import router as channels_router\n",
    '''from app.api.acd_routes import router as acd_v2_router
from app.api.agent_assist_routes import router as assist_v2_router
from app.api.agent_states_v2_routes import router as agent_states_v2_router
from app.api.dialer_v2_routes import router as dialer_v2_router
from app.api.softphone_routes import router as softphone_v2_router
from app.api.supervisor_v2_routes import router as supervisor_v2_router
from app.api.ws.assist_ws import router as assist_ws_v2_router
from app.api.ws.supervisor_ws import router as supervisor_ws_v2_router
from app.contact_center_v2.softphone.websocket import router as softphone_ws_v2_router
from app.channels.messaging import router as channels_router
''',
)
edit(
    "app/main.py",
    "router-mounts",
    "app.include_router(sms_router)\n",
    '''app.include_router(dialer_v2_router)
app.include_router(softphone_v2_router)
app.include_router(softphone_ws_v2_router)
app.include_router(assist_v2_router)
app.include_router(acd_v2_router)
app.include_router(agent_states_v2_router)
app.include_router(supervisor_v2_router)
app.include_router(assist_ws_v2_router)
app.include_router(supervisor_ws_v2_router)
app.include_router(sms_router)
''',
)

# Behavior-neutral formatting repairs required by the repository-wide Ruff
# gate. No historical migration semantics or report content is changed.
edit(
    "alembic/versions/0035_compliance_roi_deployment.py",
    "ruff-legacy-migration-aliases",
    "U=sa.Uuid; S=sa.String; J=sa.JSON; D=sa.DateTime(timezone=True)\n",
    "U = sa.Uuid\nS = sa.String\nJ = sa.JSON\nD = sa.DateTime(timezone=True)\n",
)
edit(
    "alembic/versions/0035_compliance_roi_deployment.py",
    "ruff-legacy-migration-rls-loop",
    '''):_tenant_rls(table)
''',
    '''):
            _tenant_rls(table)
''',
)
edit(
    "alembic/versions/0035_compliance_roi_deployment.py",
    "ruff-legacy-migration-drop-loop",
    "    for table in tables: op.drop_table(table)\n",
    "    for table in tables:\n        op.drop_table(table)\n",
)
edit(
    "reports/part0-reaudit/build_delivery.py",
    "ruff-legacy-report-unused",
    "    html = json.loads((ROOT / \"reports/part0-reaudit/unreferenced-html-audit.json\").read_text())\n",
    "    _html = json.loads((ROOT / \"reports/part0-reaudit/unreferenced-html-audit.json\").read_text())\n",
)
edit(
    "reports/part0-reaudit/build_delivery.py",
    "ruff-legacy-report-fstring",
    '        f"Fifteen HTML mockups were separately checked for exact filename references in the dashboard tree and for corresponding built output; none had references or appeared in the shipped build. They are archived intact under `docs/archive/dashboard-static-mockups/`, not deleted. The live React/TypeScript login, signup and routed pages remain. The audit records each pre-move SHA-256 at `reports/part0-reaudit/unreferenced-html-audit.json`.",\n',
    '        "Fifteen HTML mockups were separately checked for exact filename references in the dashboard tree and for corresponding built output; none had references or appeared in the shipped build. They are archived intact under `docs/archive/dashboard-static-mockups/`, not deleted. The live React/TypeScript login, signup and routed pages remain. The audit records each pre-move SHA-256 at `reports/part0-reaudit/unreferenced-html-audit.json`.",\n',
)

# Durable job vocabulary and registration.
edit(
    "app/jobs/types.py",
    "job-types",
    '    ACD_CALLBACK = "acd_callback"\n',
    '''    ACD_CALLBACK = "acd_callback"
    CC2_DIALER_START = "cc2_dialer_start"
    CC2_DIALER_TICK = "cc2_dialer_tick"
    CC2_ASSIST_TURN = "cc2_assist_turn"
''',
)
edit(
    "app/jobs/types.py",
    "job-registry",
    "            ACD_CALLBACK,\n",
    '''            ACD_CALLBACK,
            CC2_DIALER_START,
            CC2_DIALER_TICK,
            CC2_ASSIST_TURN,
''',
)
edit(
    "scripts/scheduler.py",
    "job-handlers",
    "    from app.outbox import dispatcher as _outbox_dispatcher  # noqa: F401 (registers handler)\n",
    '''    from app.outbox import dispatcher as _outbox_dispatcher  # noqa: F401 (registers handler)
    from app.contact_center_v2 import jobs as _cc2_jobs  # noqa: F401 (registers handlers)
''',
)
edit(
    "scripts/scheduler.py",
    "maintenance-loop",
    "async def outbox_dispatch_loop() -> None:\n",
    '''async def contact_center_v2_maintenance_loop() -> None:
    """Run the CC2 maintenance worker inside the existing scheduler."""
    from app.contact_center_v2.settings import settings
    from app.contact_center_v2.worker import maintenance_loop
    if not settings.maintenance_enabled:
        return
    await maintenance_loop(stop=_stop, session_factory=get_sessionmaker(), sleep=_sleep)


''',
    mode="before",
)
edit(
    "scripts/scheduler.py",
    "maintenance-gather",
    "        durable_jobs_loop(), outbox_dispatch_loop(),\n",
    '''        durable_jobs_loop(), outbox_dispatch_loop(),
        contact_center_v2_maintenance_loop(),
''',
)

# Canonical ACD routing gains an opt-in softphone requirement. Existing
# callers retain their behavior; only CC2 passes require_softphone=True.
edit(
    "app/contact_center/service.py",
    "candidate-signature",
    "async def build_candidates(session: AsyncSession, queue: Queue, *, now: datetime):\n",
    "async def build_candidates(\n    session: AsyncSession, queue: Queue, *, now: datetime, require_softphone: bool = False\n):\n",
)
edit(
    "app/contact_center/service.py",
    "candidate-softphone",
    '''        if not reason:
            reason = presence_reason(presence, now=now)
        held = await agent_skills(session, queue.tenant_id, member.user_id)
''',
    '''        if not reason:
            reason = presence_reason(presence, now=now)
        if not reason and require_softphone:
            from app.contact_center_v2.occupancy import active_softphone_session
            registration = await active_softphone_session(
                session, queue.tenant_id, member.user_id, now=now
            )
            if registration is None or registration.environment_id != queue.environment_id:
                reason = "softphone_required"
        held = await agent_skills(session, queue.tenant_id, member.user_id)
''',
)
edit(
    "app/contact_center/service.py",
    "enqueue-signature",
    '''async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    call_id: uuid.UUID,
    priority: int = 0,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> RouteResult:
''',
    '''async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    call_id: uuid.UUID,
    priority: int = 0,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
    require_softphone: bool = False,
) -> RouteResult:
''',
)
edit(
    "app/contact_center/service.py",
    "assign-next-signature",
    '''async def assign_next(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> RouteResult:
''',
    '''async def assign_next(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
    require_softphone: bool = False,
) -> RouteResult:
''',
)
edit(
    "app/contact_center/service.py",
    "route-entry-signature",
    '''async def _route_entry(
    session: AsyncSession,
    queue: Queue,
    entry: QueueEntry,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    strict: bool = False,
) -> RouteResult:
''',
    '''async def _route_entry(
    session: AsyncSession,
    queue: Queue,
    entry: QueueEntry,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    strict: bool = False,
    require_softphone: bool = False,
) -> RouteResult:
''',
)
edit(
    "app/contact_center/service.py",
    "route-candidates",
    "    candidates = [] if full else await build_candidates(session, queue, now=now)\n",
    "    candidates = [] if full else await build_candidates(\n        session, queue, now=now, require_softphone=require_softphone\n    )\n",
)
edit(
    "app/contact_center/service.py",
    "enqueue-existing-route",
    "            return await _route_entry(session, queue, existing, actor_id=actor_id, now=moment)\n",
    "            return await _route_entry(\n                session, queue, existing, actor_id=actor_id, now=moment,\n                require_softphone=require_softphone,\n            )\n",
)
edit(
    "app/contact_center/service.py",
    "enqueue-route",
    "    return await _route_entry(session, queue, entry, actor_id=actor_id, now=moment)\n",
    "    return await _route_entry(\n        session, queue, entry, actor_id=actor_id, now=moment,\n        require_softphone=require_softphone,\n    )\n",
)
edit(
    "app/contact_center/service.py",
    "assign-next-route",
    "    return await _route_entry(session, queue, waiting[0], actor_id=actor_id, now=moment, strict=True)\n",
    "    return await _route_entry(\n        session, queue, waiting[0], actor_id=actor_id, now=moment, strict=True,\n        require_softphone=require_softphone,\n    )\n",
)

# CC2 queue requirements retain the canonical name gate and add its durable
# minimum-proficiency gate without changing legacy queues.
edit(
    "app/contact_center/service.py",
    "candidate-proficiency-config",
    "    required = [str(name).strip().lower() for name in (queue.required_skills or [])]\n",
    "    required = [str(name).strip().lower() for name in (queue.required_skills or [])]\n    minimum_proficiency = dict((queue.overflow_policy or {}).get(\"cc2_min_proficiency\", {}))\n",
)
edit(
    "app/contact_center/service.py",
    "candidate-proficiency-gate",
    "            reason = skill_reason(required, held, by_name)\n",
    '''            reason = skill_reason(required, held, by_name)
        if not reason:
            for skill_id, minimum in minimum_proficiency.items():
                try:
                    skill_uuid = uuid.UUID(str(skill_id))
                    threshold = int(minimum)
                except (TypeError, ValueError):
                    reason = "invalid_skill_requirement"
                    break
                match = held.get(skill_uuid)
                if match is None or not match.enabled or match.proficiency < threshold:
                    reason = "insufficient_proficiency"
                    break
''',
)

# Agent-state and reservation accounting hooks record real state intervals;
# they do not infer occupancy from login or heartbeat events.
edit(
    "app/contact_center/agent_state.py",
    "state-period-capture",
    '''    """Apply one transition. Returns ``applied``, ``duplicate`` or raises."""
    if not legal(row.state, target, supervisor=supervisor):
''',
    '''    """Apply one transition. Returns ``applied``, ``duplicate`` or raises."""
    previous_state = row.state
    previous_changed_at = row.state_changed_at
    if not legal(row.state, target, supervisor=supervisor):
''',
)
edit(
    "app/contact_center/agent_state.py",
    "state-period-write",
    '''    await session.refresh(row)
    if target in RELEASE_ON:
''',
    '''    await session.refresh(row)
    if row.state != previous_state:
        from app.contact_center_v2.occupancy import record_transition
        await record_transition(
            session,
            presence=row,
            previous_state=previous_state,
            previous_changed_at=previous_changed_at,
            changed_at=row.state_changed_at,
            source_id="agent_state",
        )
    if target in RELEASE_ON:
''',
)
edit(
    "app/contact_center/repository.py",
    "reservation-period-capture",
    '''async def reserve_agent(session: AsyncSession, row: AgentPresence, *, now: datetime) -> None:
    expected = row.version
''',
    '''async def reserve_agent(session: AsyncSession, row: AgentPresence, *, now: datetime) -> None:
    previous_state = row.state
    previous_changed_at = row.state_changed_at
    expected = row.version
''',
)
edit(
    "app/contact_center/repository.py",
    "reservation-period-write",
    '''    await session.refresh(row)


async def insert_assignment(
''',
    '''    await session.refresh(row)
    if row.state != previous_state:
        from app.contact_center_v2.occupancy import record_transition
        await record_transition(
            session,
            presence=row,
            previous_state=previous_state,
            previous_changed_at=previous_changed_at,
            changed_at=row.state_changed_at,
            source_id="presence_reservation",
        )


async def insert_assignment(
''',
)

# The existing outbound service remains the sole carrier path. CC2 only
# overrides its verified caller ID and signed answer URL.
edit(
    "app/telephony/outbound.py",
    "outbound-signature",
    '''async def place_call(
    session: AsyncSession,
    tenant: Tenant,
    campaign: Campaign,
    lead: Lead,
    *,
    dry_run: bool = False,
) -> dict:
''',
    '''async def place_call(
    session: AsyncSession,
    tenant: Tenant,
    campaign: Campaign,
    lead: Lead,
    *,
    dry_run: bool = False,
    answer_url: str | None = None,
    caller_id_number: str | None = None,
) -> dict:
''',
)
edit(
    "app/telephony/outbound.py",
    "outbound-caller-id",
    '''    await session.refresh(lead)

    caller_id = tenant.outbound_caller_id or tenant.twilio_number
''',
    '''    await session.refresh(lead)

    caller_id = caller_id_number or tenant.outbound_caller_id or tenant.twilio_number
''',
)
edit(
    "app/telephony/outbound.py",
    "outbound-answer-url",
    "            url=build_outbound_twiml_url(str(campaign.id), str(lead.id)),\n",
    "            url=answer_url or build_outbound_twiml_url(str(campaign.id), str(lead.id)),\n",
)

# Final monitor events feed only an already-active assist session. Redaction
# and durable writes live in the new module; existing monitor fanout is intact.
edit(
    "app/telephony/monitor_bus.py",
    "assist-capture",
    '''        """Publish a transcript event if and only if listeners exist. Never logs transcript PII."""
        cid = str(call_id)
''',
    '''        """Publish a transcript event if and only if listeners exist. Never logs transcript PII."""
        if is_final and text.strip():
            from app.contact_center_v2.assist import capture_transcript
            await capture_transcript(
                call_id, text, speaker=speaker, is_final=True, seq=seq
            )
        cid = str(call_id)
''',
)

# Signed Twilio branches. These anchors are after the existing signature
# verifier, never on a public unauthenticated webhook path.
edit(
    "app/telephony/twilio_handler.py",
    "voice-cc2",
    '''    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    tenant = await find_tenant_for_called_number(session, To)
''',
    '''    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    # CC2 client calls have no called-number tenant. Their signed branch does
    # its own tenant/session reservation check before the inbound E.164 guard.
    from app.contact_center_v2.telephony import handle_voice
    cc2_voice = await handle_voice(
        request, session, call_sid=CallSid, from_uri=From
    )
    if cc2_voice is not None:
        return cc2_voice
    tenant = await find_tenant_for_called_number(session, To)
''',
)
edit(
    "app/telephony/twilio_handler.py",
    "outbound-cc2",
    '''    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    response = VR()
''',
    '''    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    from app.contact_center_v2.telephony import handle_outbound_answer
    cc2_answer = await handle_outbound_answer(
        request,
        session,
        campaign_id=campaign_id,
        call_sid=CallSid,
        answered_by=AnsweredBy,
    )
    if cc2_answer is not None:
        return cc2_answer
    response = VR()
''',
)
edit(
    "app/telephony/twilio_handler.py",
    "status-amd-argument",
    '''    CallDuration: str = Form(default="0"),
    session: AsyncSession = Depends(get_session),
''',
    '''    CallDuration: str = Form(default="0"),
    AnsweredBy: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
''',
)
edit(
    "app/telephony/twilio_handler.py",
    "status-cc2-unknown",
    '''    if call is None:
        # Unknown SID: acknowledge so Twilio stops retrying, but do nothing.
        log.info("status.unknown_call_sid", call_sid=CallSid)
        return PlainTextResponse("ok")
''',
    '''    if call is None:
        from app.contact_center_v2.telephony import reconcile_status
        try:
            cc2_duration = max(0, int(float(CallDuration or 0)))
        except (TypeError, ValueError):
            cc2_duration = 0
        cc2_row = await reconcile_status(
            session,
            provider_call_id=CallSid,
            provider_status=CallStatus_,
            duration_seconds=cc2_duration,
            answered_by=AnsweredBy,
        )
        if cc2_row is not None:
            await session.commit()
            return PlainTextResponse("ok")
        # Unknown SID: acknowledge so Twilio stops retrying, but do nothing.
        log.info("status.unknown_call_sid", call_sid=CallSid)
        return PlainTextResponse("ok")
''',
)
edit(
    "app/telephony/twilio_handler.py",
    "status-cc2-known",
    '''    await session.commit()

    if result.applied:
        await realtime_events.emit_call_event(call, kind="call.updated")
''',
    '''    from app.contact_center_v2.telephony import reconcile_status
    await reconcile_status(
        session,
        provider_call_id=CallSid,
        provider_status=CallStatus_,
        duration_seconds=int(duration),
        answered_by=AnsweredBy,
    )
    await session.commit()

    if result.applied:
        await realtime_events.emit_call_event(call, kind="call.updated")
''',
)

# Helm values are insertions, not copies of shared files.
edit(
    "infra/helm/voxdesk/values.yaml",
    "softphone-values",
    '''config:
  appEnv: production
''',
    '''config:
  appEnv: production
  softphoneTwimlAppSid: ""
  softphoneTwilioApiKeySid: ""
''',
)
edit(
    "infra/helm/voxdesk/values.yaml",
    "coturn-values",
    '''scheduler:
  enabled: true
''',
    '''coturn:
  enabled: false
  image: coturn/coturn:4.7.0
  replicas: 1
  externalIP: ""
  realm: turn.example.com
  secretName: voxdesk-runtime
  secretKey: CC2_TURN_STATIC_SECRET
  minPort: 49152
  maxPort: 49252
  tls:
    enabled: false
    secretName: ""
  service:
    type: LoadBalancer
  resources:
    requests: {cpu: 100m, memory: 128Mi}
    limits: {cpu: "1", memory: 512Mi}
scheduler:
  enabled: true
''',
)
edit(
    "infra/helm/voxdesk/values.yaml",
    "worker-values",
    '''scheduler:
  enabled: true
''',
    '''contactCenterWorkers:
  enabled: false
  terminationGracePeriodSeconds: 45
  pdb:
    maxUnavailable: 1
  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {cpu: "1", memory: 1Gi}
scheduler:
  enabled: true
''',
)
edit(
    "infra/helm/voxdesk/templates/scheduler.yaml",
    "worker-maintenance-env",
    '''            - name: SHUTDOWN_DRAIN_TIMEOUT_SECONDS
''',
    '''            - name: CC2_MAINTENANCE_ENABLED
              value: {{ not .Values.contactCenterWorkers.enabled | quote }}
            - name: SHUTDOWN_DRAIN_TIMEOUT_SECONDS
''',
    mode="before",
)
edit(
    "infra/helm/voxdesk/templates/api.yaml",
    "softphone-env",
    '''            - name: SHUTDOWN_DRAIN_TIMEOUT_SECONDS
              value: {{ .Values.drain.timeoutSeconds | default 45 | quote }}
''',
    '''            - name: CC2_SOFTPHONE_TWILIO_TWIML_APP_SID
              value: {{ .Values.config.softphoneTwimlAppSid | quote }}
            - name: CC2_SOFTPHONE_TWILIO_API_KEY_SID
              value: {{ .Values.config.softphoneTwilioApiKeySid | quote }}
            {{- if .Values.coturn.enabled }}
            - name: CC2_STUN_URL
              value: {{ printf "stun:%s:3478" .Values.coturn.externalIP | quote }}
            - name: CC2_TURN_UDP_URL
              value: {{ printf "turn:%s:3478?transport=udp" .Values.coturn.externalIP | quote }}
            - name: CC2_TURN_TCP_URL
              value: {{ printf "turn:%s:3478?transport=tcp" .Values.coturn.externalIP | quote }}
            {{- if .Values.coturn.tls.enabled }}
            - name: CC2_TURNS_URL
              value: {{ printf "turns:%s:5349?transport=tcp" .Values.coturn.externalIP | quote }}
            {{- end }}
            {{- end }}
            - name: SHUTDOWN_DRAIN_TIMEOUT_SECONDS
              value: {{ .Values.drain.timeoutSeconds | default 45 | quote }}
''',
)

# Validate all anchors first.
for item in edits:
    path = root / item.path
    if not path.is_file():
        raise SystemExit(f"BATCH04: missing shared file {item.path}")
    text = path.read_text()
    begin = f"# BATCH04:BEGIN {item.name}"
    end = f"# BATCH04:END {item.name}"
    if (begin in text) != (end in text):
        raise SystemExit(f"BATCH04: half marker in {item.path}: {item.name}")
    if begin not in text and text.count(item.anchor) != 1:
        raise SystemExit(
            f"BATCH04: anchor mismatch in {item.path}: {item.name}; "
            f"expected exactly one, found {text.count(item.anchor)}"
        )

for item in edits:
    path = root / item.path
    text = path.read_text()
    indent = item.anchor[: len(item.anchor) - len(item.anchor.lstrip(" "))]
    begin = f"{indent}# BATCH04:BEGIN {item.name}"
    if begin in text:
        continue
    insertion = block(indent, item.name, item.replacement)
    if item.mode == "replace":
        text = text.replace(item.anchor, insertion, 1)
    elif item.mode == "before":
        text = text.replace(item.anchor, insertion + item.anchor, 1)
    elif item.mode == "after":
        text = text.replace(item.anchor, item.anchor + insertion, 1)
    else:
        raise AssertionError(item.mode)
    path.write_text(text)

# Append-only shared includes have a real end-of-file anchor and are guarded
# by the same complete-marker rule.
makefile = root / "Makefile"
text = makefile.read_text()
if "# BATCH04:BEGIN make-fragment" not in text:
    if not text.endswith("\n"):
        text += "\n"
    text += "\n# BATCH04:BEGIN make-fragment\ninclude Makefile.batch04.mk\n# BATCH04:END make-fragment\n"
    makefile.write_text(text)
elif "# BATCH04:END make-fragment" not in text:
    raise SystemExit("BATCH04: half marker in Makefile")
PY

# Copy only files owned by Batch 04. Existing different files are never
# overwritten; identical files make reruns harmless.
while IFS= read -r -d '' source; do
  relative="${source#"$PATCH_DIR"/}"
  [[ "$relative" == "apply_batch04.sh" ]] && continue
  destination="$REPO_DIR/$relative"
  if [[ -e "$destination" ]] && ! cmp -s "$source" "$destination"; then
    echo "BATCH04: refusing to overwrite different existing file: $relative" >&2
    exit 6
  fi
  mkdir -p "$(dirname "$destination")"
  cp "$source" "$destination"
done < <(find "$PATCH_DIR" -type f \
  ! -path '*/__pycache__/*' ! -path '*/node_modules/*' ! -path '*/dist/*' \
  ! -path '*/.venv/*' ! -path '*/.git/*' ! -path '*/.next/*' \
  ! -path '*/.ruff_cache/*' ! -path '*/.pytest_cache/*' \
  ! -path '*/coverage/*' ! -path '*/build/*' \
  ! -name '*.pyc' ! -name '.DS_Store' -print0)

cd "$REPO_DIR"
export PYTHONDONTWRITEBYTECODE=1
python3 -m compileall -q app/contact_center_v2 app/api/ws app/api/dialer_v2_routes.py app/api/softphone_routes.py app/api/agent_assist_routes.py app/api/acd_routes.py app/api/agent_states_v2_routes.py app/api/supervisor_v2_routes.py app/db/contact_center_v2_models.py

# Regenerate reviewed additive API artifacts from the mounted application;
# no generated JSON or shared snapshot is shipped as a full-file replacement.
python3 - <<'PY'
import json
from pathlib import Path
from app.main import app

root = Path.cwd()
schema = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
(root / "docs/api").mkdir(parents=True, exist_ok=True)
(root / "contracts/openapi.json").write_text(schema)
(root / "docs/api/openapi.json").write_text(schema)
routes = []
for route in app.routes:
    path = getattr(route, "path", None)
    if path is None:
        continue
    methods = sorted(getattr(route, "methods", None) or [])
    if isinstance(route, __import__("starlette.routing", fromlist=["Route"]).Route) and "GET" in methods:
        if "HEAD" not in methods:
            methods.append("HEAD")
            methods.sort()
    routes.append({"methods": methods, "name": getattr(route, "name", ""), "path": path})
snapshot = {
    "dashboard_build_present": Path("dashboard/dist/index.html").is_file(),
    "route_count": len(routes),
    "routes": routes,
}
(root / "tests/truth/routes_snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
PY

# Build the repository's self-contained API reference as well as the machine
# contract. This generates shared artifacts locally; none are shipped as copies.
bash scripts/build_api_docs.sh

# README statistics are generated from the mounted app and collected tests;
# this is the repository's existing generator, not a shipped shared-file copy.
python3 scripts/repo_stats.py --write-readme

# Migration precondition and single-head gate. The installer refuses to
# silently jump a database that has not reached 0065.
current="$(python3 -m alembic current 2>/dev/null | awk 'match($0, /[0-9]{4}_[A-Za-z0-9_]+/) { print substr($0, RSTART, RLENGTH); exit }')"
if [[ "$current" != "0065_compliance_v2" ]] && [[ "$current" != "0066_contact_center_v2" ]]; then
  echo "BATCH04: database is at '${current:-uninitialized}' instead of 0065_compliance_v2 or an already-applied 0066_contact_center_v2." >&2
  exit 7
fi
heads="$(python3 -m alembic heads | awk 'NF { count++ } END { print count+0 }')"
[[ "$heads" == "1" ]] || { echo "BATCH04: expected one Alembic head, found $heads" >&2; exit 8; }
python3 -m alembic upgrade head
python3 -m alembic check

# Required non-live backend, truth, contract, SDK, and dashboard gates. Live
# carrier tests skip cleanly unless VOXDESK_LIVE_TESTS=1 is explicitly set.
make batch04-check
