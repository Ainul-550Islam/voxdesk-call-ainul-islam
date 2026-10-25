import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.contact_center_v2.errors import ProviderFailure
from app.contact_center_v2.softphone.service import control_call
from app.db.contact_center_v2_models import AgentStatePeriod, SoftphoneSession
from app.db.models import User
from tests.contact_center_v2.test_softphone import make_connected_call


class Participant:
    def __init__(self, adapter, provider_id):
        self.adapter = adapter
        self.provider_id = provider_id

    def update(self, *, muted):
        self.adapter.mutations.append((self.provider_id, muted))
        if self.adapter.fail:
            raise RuntimeError("provider refused participant update")
        return object()


class Conferences:
    def __init__(self, adapter):
        self.adapter = adapter

    def __call__(self, conference_sid):
        assert conference_sid == "CF-TEST"
        return self

    def participants(self, provider_id):
        return Participant(self.adapter, provider_id)


class Adapter:
    def __init__(self):
        self.mutations = []
        self.fail = False
        self.provider = type("Provider", (), {})()
        self.provider.conferences = Conferences(self)

    def require_configured(self):
        return None

    def _client(self):
        return self.provider


async def test_hold_mute_replays_do_not_repeat_provider_or_occupancy_transitions(
    db: AsyncSession,
    cc2_scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call, _ = await make_connected_call(db, cc2_scope, agent_a, softphone_registration)
    call.provider_conference_id = "CF-TEST"
    await db.commit()
    context = TenantContext(user=agent_a, tenant=cc2_scope.tenant)
    adapter = Adapter()
    before_periods = await db.scalar(select(func.count()).select_from(AgentStatePeriod))

    held = await control_call(db, context, call_id=call.id, action="hold", enabled=True, adapter=adapter)
    held_replay = await control_call(db, context, call_id=call.id, action="hold", enabled=True, adapter=adapter)
    resumed = await control_call(db, context, call_id=call.id, action="hold", enabled=False, adapter=adapter)
    resumed_replay = await control_call(db, context, call_id=call.id, action="hold", enabled=False, adapter=adapter)
    muted = await control_call(db, context, call_id=call.id, action="mute", enabled=True, adapter=adapter)
    muted_replay = await control_call(db, context, call_id=call.id, action="mute", enabled=True, adapter=adapter)
    assert held_replay is held and resumed_replay is resumed and muted_replay is muted
    assert call.on_hold is False and call.muted is True and call.status == "in_progress"
    assert len(adapter.mutations) == 3
    after_periods = await db.scalar(select(func.count()).select_from(AgentStatePeriod))
    assert after_periods - before_periods == 2


async def test_provider_refusal_does_not_claim_a_new_hold_state(
    db: AsyncSession,
    cc2_scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call, _ = await make_connected_call(db, cc2_scope, agent_a, softphone_registration)
    call.provider_conference_id = "CF-TEST"
    await db.commit()
    adapter = Adapter()
    adapter.fail = True
    with pytest.raises(ProviderFailure):
        await control_call(
            db,
            TenantContext(user=agent_a, tenant=cc2_scope.tenant),
            call_id=call.id,
            action="hold",
            enabled=True,
            adapter=adapter,
        )
    assert call.on_hold is False
    assert call.status == "in_progress"
