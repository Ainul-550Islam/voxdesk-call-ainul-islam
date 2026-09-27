"""P0 master consolidation — runtime enforcement proof.

``test_ai_runtime_enforcement.py`` (Batch 04) proves the *denial matrix*:
which refusals happen before a provider may run. This file proves the
reservation lifecycle that connects those denials to the billing admission
counter, which the consolidation contract requires end to end:

    admit -> reserve -> success / reconcile
                     -> failure / release

A denied budget, an open circuit, a non-retryable provider failure, an
executor that never runs and a crashed text turn must all give the reserved
tokens back; a completed call must swap its estimate for the tokens the
provider actually measured. Without that, every failed call permanently
shrinks the tenant's admission ceiling — a phantom reservation.

Two structural invariants are proved by source inspection because Pipecat
and the provider SDKs cannot be imported in this environment: the voice
media chain really wires the governed hearing/speech processors, and the
gateway never imports the runtime (the no-recursion rule).

No provider SDK is contacted. ``build_llm`` is never reached.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent.errors import ProviderAuthenticationError, ProviderTimeoutError
from app.agent.text_agent import TextAgent
from app.ai import circuit_breaker
from app.ai.budget import admit, reconcile, release
from app.ai.fallback import next_candidate
from app.ai.gateway import LiveAuthorization, authorize_live, govern, load_policy, save_policy
from app.ai.models import AIAdmissionCounter, GovernanceError, PolicyDenied, PolicyView
from app.ai.runtime import invoke, prepare_voice
from app.auth.dependencies import TenantContext
from app.core.config import settings
from app.db.models import UsageEvent, UsageMetric
from app.tenancy.isolation import ValidationFailed
from tests.acd_support import live_call, production

pytestmark = pytest.mark.asyncio


def _ctx(tenant, user) -> TenantContext:
    return TenantContext(user=user, tenant=tenant, organization_id=tenant.organization_id)


@pytest.fixture(autouse=True)
def _reset_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


async def _reserved(db, tenant) -> int:
    """The tenant's live reservation, read fresh (Core UPDATEs do not expire ORM state)."""
    row = await db.get(AIAdmissionCounter, tenant.id)
    if row is None:
        return 0
    await db.refresh(row)
    return row.tokens_reserved


def _timing_out_executor(providers_seen: list):
    async def executor(choice, text, timeout_ms):
        providers_seen.append(choice.provider)
        raise ProviderTimeoutError("slow", provider=choice.provider)

    return executor


# ------------------------------------------------- reservation: invoke path ---

async def test_failed_invocation_releases_the_reservation(db, tenant_a, owner_a):
    """A provider failure after admission gives the estimate back."""
    tenant_a.llm_preset = "fast"
    await save_policy(
        db, tenant_a.id, token_ceiling=10, ceiling_supplied=True,
        disabled_providers=["anthropic", "google"],
    )
    await db.commit()
    seen: list = []
    with pytest.raises(ProviderTimeoutError):
        await invoke(
            db,
            _ctx(tenant_a, owner_a),
            text="hello",
            executor=_timing_out_executor(seen),
            tokens_estimate=5,
        )
    assert seen == ["openai"]  # no fallback to a disabled provider, one attempt
    assert await _reserved(db, tenant_a) == 0


async def test_successful_invocation_reconciles_estimate_to_measured_tokens(
    db, tenant_a, owner_a
):
    tenant_a.llm_preset = "fast"
    await save_policy(db, tenant_a.id, token_ceiling=100, ceiling_supplied=True)
    await db.commit()

    async def executor(choice, text, timeout_ms):
        return {"text": "ok", "tokens": 42}

    result = await invoke(
        db,
        _ctx(tenant_a, owner_a),
        text="hello",
        executor=executor,
        tokens_estimate=5,
    )
    assert result.executed is True
    # The estimate (5) became the measurement (42) — not 5, not 47.
    assert await _reserved(db, tenant_a) == 42


async def test_executor_not_attached_releases_the_reservation(db, tenant_a, owner_a):
    """No provider ran, so no reservation may survive."""
    tenant_a.llm_preset = "fast"
    await save_policy(db, tenant_a.id, token_ceiling=10, ceiling_supplied=True)
    await db.commit()
    result = await invoke(
        db,
        _ctx(tenant_a, owner_a),
        text="hello",
        executor=None,
        tokens_estimate=5,
    )
    assert result.executed is False
    assert await _reserved(db, tenant_a) == 0


async def test_circuit_open_after_admission_releases_the_reservation(db, tenant_a, owner_a):
    """The circuit consult runs *after* budget admission inside authorize_live;
    its refusal must unwind the reservation it just made."""
    tenant_a.llm_preset = "fast"
    await save_policy(db, tenant_a.id, token_ceiling=10, ceiling_supplied=True)
    await db.commit()
    for _ in range(5):
        circuit_breaker.record_failure("openai")
    seen: list = []
    with pytest.raises(GovernanceError) as caught:
        await invoke(
            db,
            _ctx(tenant_a, owner_a),
            text="hello",
            executor=_timing_out_executor(seen),
            tokens_estimate=5,
        )
    assert caught.value.code == "provider_unavailable"
    assert seen == []
    assert await _reserved(db, tenant_a) == 0


async def test_ceiling_unknown_reserves_nothing_and_release_is_a_noop(db, tenant_a, owner_a):
    """Without a configured ceiling there is no reservation — and a release
    must not invent a counter row or drive anybody negative."""

    async def executor(choice, text, timeout_ms):
        return {"text": "ok", "tokens": 9}

    result = await invoke(
        db, _ctx(tenant_a, owner_a), text="hello", executor=executor, tokens_estimate=5
    )
    assert result.executed is True
    assert await db.get(AIAdmissionCounter, tenant_a.id) is None
    await release(db, tenant_a.id, 5)  # no row: a silent no-op, never an insert
    assert await db.get(AIAdmissionCounter, tenant_a.id) is None


# --------------------------------------------- reservation: authorize/voice ---

async def test_authorize_live_flags_whether_it_reserved(db, tenant_a):
    env = await production(db, tenant_a)
    live = await authorize_live(
        db, tenant_a, channel="voice", environment_kind="production",
        environment_id=env.id, check_input=False, tokens_estimate=1,
        principal="agent_runtime",
    )
    assert isinstance(live, LiveAuthorization)
    assert live.budget_reserved is False  # no ceiling configured -> nothing reserved

    await save_policy(db, tenant_a.id, token_ceiling=25, ceiling_supplied=True)
    await db.commit()
    live = await authorize_live(
        db, tenant_a, channel="voice", environment_kind="production",
        environment_id=env.id, check_input=False, tokens_estimate=1,
        principal="agent_runtime",
    )
    assert live.budget_reserved is True
    assert await _reserved(db, tenant_a) == 1


async def test_voice_preparation_carries_the_reservation_flag(db, tenant_a):
    """The voice pipeline reconciles at call end, so ``prepare_voice`` must
    tell it whether a reservation exists."""
    await save_policy(db, tenant_a.id, token_ceiling=25, ceiling_supplied=True)
    await db.commit()
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    prepared = await prepare_voice(db, tenant_a, call)
    assert prepared.budget_reserved is True
    assert prepared.environment_kind == "production"
    assert await _reserved(db, tenant_a) == 1


# ------------------------------------------------ reservation: govern direct ---

async def test_govern_direct_path_releases_on_a_non_retryable_failure(
    db, tenant_a, owner_a
):
    """Callers that use ``govern`` without the runtime (QA auto-review) get
    the same lifecycle: a non-retryable failure releases what admit reserved."""
    tenant_a.llm_preset = "fast"
    await save_policy(db, tenant_a.id, token_ceiling=10, ceiling_supplied=True)
    await db.commit()

    async def executor(choice, text, timeout_ms):
        raise ProviderAuthenticationError("bad key", provider=choice.provider)

    with pytest.raises(ProviderAuthenticationError):
        await govern(
            db,
            _ctx(tenant_a, owner_a),
            text="hello",
            channel="async",
            executor=executor,
            tokens_estimate=4,
        )
    assert await _reserved(db, tenant_a) == 0


async def test_fallback_candidates_are_independently_policy_valid(db, tenant_a, owner_a):
    """Every fallback candidate must pass the same policy gate; a disabled
    provider is never a landing spot, so the failure surfaces instead."""
    tenant_a.llm_preset = "fast"
    await save_policy(
        db, tenant_a.id, disabled_providers=["anthropic", "google"],
        token_ceiling=10, ceiling_supplied=True,
    )
    await db.commit()
    policy = await load_policy(db, tenant_a.id)
    from app.agent.llm_factory import PRESETS

    assert next_candidate(PRESETS["fast"], policy, environment_kind="production") is None
    seen: list = []
    with pytest.raises(ProviderTimeoutError):
        await govern(
            db,
            _ctx(tenant_a, owner_a),
            text="hello",
            channel="async",
            executor=_timing_out_executor(seen),
            tokens_estimate=2,
        )
    assert seen == ["openai"]
    assert await _reserved(db, tenant_a) == 0


async def test_release_and_reconcile_never_go_negative(db, tenant_a):
    await save_policy(db, tenant_a.id, token_ceiling=100, ceiling_supplied=True)
    await db.commit()
    policy = await load_policy(db, tenant_a.id)
    decision = await admit(db, tenant_a, policy, tokens=10)
    assert decision.reason == "reserved"

    await release(db, tenant_a.id, 25)  # more than reserved -> floored at zero
    assert await _reserved(db, tenant_a) == 0

    await reconcile(db, tenant_a.id, reserved=5, actual=40)  # +35
    assert await _reserved(db, tenant_a) == 35

    await reconcile(db, tenant_a.id, reserved=100, actual=1)  # floored at zero
    assert await _reserved(db, tenant_a) == 0

    with pytest.raises(ValidationFailed):
        await reconcile(db, tenant_a.id, reserved=1, actual=-5)


# ------------------------------------------------- reservation: text replies ---

def _text_agent(db, tenant, call) -> TextAgent:
    tenant.llm_preset = "fast"
    return TextAgent(tenant, SimpleNamespace(session=db, call=call), channel="sms")


async def test_text_reply_failure_releases_the_admitted_token(db, tenant_a, monkeypatch):
    await save_policy(db, tenant_a.id, token_ceiling=50, ceiling_supplied=True)
    await db.commit()
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-used")
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    agent = _text_agent(db, tenant_a, call)

    async def boom(history, user_text):
        raise ProviderTimeoutError("slow", provider="openai")

    agent.complete_turn = boom
    with pytest.raises(ProviderTimeoutError):
        await agent.reply([], "are you open?")
    assert await _reserved(db, tenant_a) == 0


def _fake_openai_client(monkeypatch, *, total_tokens: int | None):
    usage = None if total_tokens is None else SimpleNamespace(total_tokens=total_tokens)

    async def create(**_kwargs):
        message = SimpleNamespace(
            content="we are open today",
            tool_calls=None,
            model_dump=lambda exclude_none=False: {
                "role": "assistant", "content": "we are open today",
            },
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(
        "app.agent.text_agent._openai_style_client", lambda provider, api_key: client
    )


async def test_text_reply_reconciles_and_records_measured_usage(db, tenant_a, monkeypatch):
    """The whole governed text seam: admission reserves one token, the
    provider measures 17, the reservation becomes 17 and the existing billing
    meter receives one usage event attributed to the call's environment."""
    await save_policy(db, tenant_a.id, token_ceiling=50, ceiling_supplied=True)
    await db.commit()
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-used")
    _fake_openai_client(monkeypatch, total_tokens=17)
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    agent = _text_agent(db, tenant_a, call)

    result = await agent.reply([], "are you open?")
    assert result["reply"] == "we are open today"
    assert result["tokens"] == 17
    assert await _reserved(db, tenant_a) == 17

    events = (
        await db.execute(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant_a.id,
                UsageEvent.metric == UsageMetric.LLM_TOKEN,
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].quantity == 17
    assert events[0].environment_id == env.id
    assert events[0].source_entity_id == call.id


async def test_complete_turn_reports_none_when_the_provider_measures_nothing(
    monkeypatch,
):
    """An unmeasured turn must report ``None``, never a fabricated zero —
    the governed boundary only records usage from a measured count."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-used")
    monkeypatch.setattr(
        "app.agent.text_agent.build_system_prompt", lambda tenant, provider=None: "rules"
    )
    _fake_openai_client(monkeypatch, total_tokens=None)
    agent = TextAgent(
        SimpleNamespace(
            id=uuid.uuid4(), name="Acme", temperature=0.4,
            llm_preset="fast", llm_provider=None, llm_model=None,
        ),
        SimpleNamespace(session=None, call=None, calls=[]),
        channel="sms",
    )
    result = await agent.complete_turn([], "hi")
    assert result["tokens"] is None


# ------------------------------------------------- structural invariants ---

def test_voice_media_chain_wires_the_guardrail_processors():
    """The hearing guard sits between STT and the LLM context; the speech
    guard sits between text normalization and TTS — blocked text is replaced
    before it can be spoken, and a refused transcript never reaches the model.
    Source-level proof because Pipecat cannot be imported here."""
    source = open("app/agent/pipeline.py", encoding="utf-8").read()
    block = source[source.index("pipeline = Pipeline("):source.index("task = PipelineTask(")]
    i_stt_usage = block.index("stt_usage,")
    i_hearing = block.index("GovernedHearing()")
    i_user_ctx = block.index("context_aggregator.user()")
    i_normalizer = block.index("TextNormalizer()")
    i_speech = block.index("GovernedSpeech(blocked_substrings=prepared.blocked_output)")
    i_tts = block.index("\n            tts,")
    assert i_stt_usage < i_hearing < i_user_ctx
    assert i_normalizer < i_speech < i_tts
    # The end-of-call reservation reconcile is wired into the persistence step.
    assert "prepared.budget_reserved" in source


def test_gateway_never_imports_the_runtime():
    """The no-recursion rule: runtime -> gateway is the only direction."""
    gateway = open("app/ai/gateway.py", encoding="utf-8").read()
    runtime = open("app/ai/runtime.py", encoding="utf-8").read()
    assert "from app.ai.runtime" not in gateway
    assert "import runtime" not in gateway
    assert "from app.ai.gateway import" in runtime


def test_policy_denial_is_never_a_fallback_trigger():
    """``next_candidate`` walks the existing order, but a policy denial is a
    refusal, not a hint to try somebody else — and every candidate it does
    return has independently passed ``assert_allowed``."""
    denied_everywhere = PolicyView(
        tenant_id=uuid.uuid4(),
        status="active",
        allowed_presets=(),
        disabled_providers=("openai", "anthropic", "google"),
        development_only_presets=(),
        token_ceiling=None,
        persisted=True,
    )
    from app.agent.llm_factory import PRESETS

    assert next_candidate(PRESETS["fast"], denied_everywhere, environment_kind="production") is None
    with pytest.raises(PolicyDenied):
        from app.ai.routing import assert_allowed

        assert_allowed(PRESETS["fast"], denied_everywhere, environment_kind="production")
