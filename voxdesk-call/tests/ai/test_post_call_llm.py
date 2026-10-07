"""Structured post-call inference contracts through the real AI gateway."""
from __future__ import annotations

import asyncio
import json
import os
import socket

import httpx
import pytest
from sqlalchemy import select

from app.agent.errors import (
    ProviderAuthenticationError, ProviderAuthorizationError, ProviderInvalidRequestError,
    ProviderRateLimitedError, ProviderUnavailableError, ProviderTimeoutError,
)
from app.agent.llm_factory import LLMChoice
from app.ai import circuit_breaker
from app.ai.gateway import save_policy
from app.ai.models import GovernanceError
from app.ai import post_call_llm as llm
from app.auth.dependencies import TenantContext
from app.core.config import settings
from app.core.ssrf import OutboundUrlError
from app.db.models import UsageEvent, UsageMetric

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"booked": {"type": "boolean"}}, "required": ["booked"]}


@pytest.fixture
def configured(monkeypatch):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "openai_api_key", "test-only-openai")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-only-anthropic")
    monkeypatch.setattr(settings, "google_api_key", "test-only-google")
    # Resolver seam only: production static and resolved IP guards still run.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    yield
    circuit_breaker.reset()


def ctx(tenant, user):
    tenant.llm_preset = "fast"
    return TenantContext(tenant=tenant, user=user, organization_id=tenant.organization_id)


def openai_response(text, tokens=9):
    result = {"choices": [{"message": {"content": text}}]}
    if tokens is not None:
        result["usage"] = {"total_tokens": tokens}
    return httpx.Response(200, json=result)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_actual_provider_request_contract(configured, provider):
    requests = []

    async def receiver(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST" and request.url.scheme == "https"
        assert "SECRET" not in str(request.url)
        if provider == "openai":
            assert request.url.host == "api.openai.com"
            assert request.headers["authorization"] == "Bearer test-only-openai"
            assert body["response_format"] == {"type": "json_object"}
            return openai_response('{"booked":true}')
        if provider == "anthropic":
            assert request.url.host == "api.anthropic.com"
            assert request.headers["x-api-key"] == "test-only-anthropic"
            assert body["tools"][0]["input_schema"] == SCHEMA
            assert body["tool_choice"]["name"] == "record_analysis"
            return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "record_analysis", "input": {"booked": True}}], "usage": {"input_tokens": 4, "output_tokens": 5}})
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "test-only-google"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"booked":true}'}]}}], "usageMetadata": {"totalTokenCount": 9}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        result = await llm.StructuredExecutor(SCHEMA, client=client)(
            LLMChoice(provider, "configured-model", 300, "test"), "Return JSON", 5000)
    assert json.loads(result["text"]) == {"booked": True}
    assert result["tokens"] == 9
    assert len(requests) == 1


async def test_gateway_retry_records_each_measured_usage(db, tenant_a, owner_a, configured):
    received = []

    async def receiver(request):
        received.append(json.loads(request.content))
        return openai_response('{"booked":"wrong type"}' if len(received) == 1 else '{"booked":true}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        output = await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "The appointment was booked.", executor=llm.StructuredExecutor(SCHEMA, client=client))
    await db.commit()
    assert output.status == "completed" and output.value == {"booked": True}
    assert len(output.invocations) == len(received) == 2
    assert all(item["tokens"] == 9 for item in output.invocations)
    assert all("provider_cost_usd" in item for item in output.invocations)
    usage = list((await db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_a.id, UsageEvent.metric == UsageMetric.LLM_TOKEN))).all())
    assert len(usage) == 2
    assert len({row.idempotency_key for row in usage}) == 2
    assert "previous output was invalid" in received[1]["messages"][0]["content"]
    assert "wrong type" not in received[1]["messages"][0]["content"]


async def test_missing_provider_never_fabricates_result(db, tenant_a, owner_a, configured, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    result = await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "A real transcript")
    assert result.status == "not_configured"
    assert result.value is None and not result.invocations
    assert result.error == "provider_not_configured"
    assert not list((await db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_a.id))).all())


@pytest.mark.parametrize("text", ['{"booked":"yes"}', '{"booked":true,"extra":7}', '{"booked":true,"booked":false}', '{"booked":NaN}', 'not JSON'])
async def test_invalid_outputs_are_bounded_not_success(db, tenant_a, owner_a, configured, text):
    calls = []

    async def executor(choice, prompt, timeout_ms):
        calls.append(prompt)
        return {"text": text, "tokens": 2}

    result = await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "Actual call text", executor=executor)
    assert result.status == "failed" and result.value is None
    assert result.error == "invalid_output_schema" and len(calls) == 2
    assert len(result.invocations) == 2


async def test_unknown_usage_is_not_zero(db, tenant_a, owner_a, configured):
    async def executor(choice, prompt, timeout_ms):
        return {"text": '{"booked":false}', "tokens": None}
    result = await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "No booking occurred", executor=executor)
    assert result.status == "completed"
    assert result.invocations[0]["tokens"] is None
    assert result.invocations[0]["cost_known"] is False
    assert result.invocations[0]["provider_cost_usd"] is None


async def test_budget_denial_precedes_provider(db, tenant_a, owner_a, configured):
    await save_policy(db, tenant_a.id, token_ceiling=0, ceiling_supplied=True)
    seen = []

    async def executor(*args):
        seen.append(args)
        raise AssertionError("Provider must not run")

    with pytest.raises(GovernanceError, match="budget"):
        await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "Transcript", executor=executor)
    assert not seen


@pytest.mark.parametrize("status,error", [(301, ProviderInvalidRequestError), (401, ProviderAuthenticationError), (403, ProviderAuthorizationError), (408, ProviderUnavailableError), (429, ProviderRateLimitedError), (503, ProviderUnavailableError)])
async def test_http_failures_not_success_or_redirect(configured, status, error):
    calls = []

    async def receiver(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "http://169.254.169.254"}, text="sensitive provider detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver), follow_redirects=True) as client:
        with pytest.raises(error) as caught:
            await llm.StructuredExecutor(SCHEMA, client=client)(LLMChoice("openai", "gpt-test", 300, "test"), "JSON", 2000)
    assert len(calls) == 1
    assert "sensitive" not in str(caught.value)


async def test_dns_private_answer_rejected_before_http(configured, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))])
    seen = []

    async def receiver(request):
        seen.append(request)
        return openai_response('{"booked":true}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        with pytest.raises(OutboundUrlError):
            await llm.StructuredExecutor(SCHEMA, client=client)(LLMChoice("openai", "gpt-test", 300, "test"), "JSON", 2000)
    assert not seen


@pytest.mark.parametrize("schema", [{"type": "array"}, {"type": "object", "$ref": "http://169.254.169.254"}, {"type": "object", "properties": {"x": {"$dynamicRef": "https://example.com/schema"}}}])
def test_nonlocal_schema_rejected(schema):
    with pytest.raises(ValueError):
        llm.validate_schema(schema)


async def test_summary_sentiment_are_real_validated_values(db, tenant_a, owner_a, configured):
    async def executor(choice, prompt, timeout_ms):
        assert timeout_ms <= 5000
        if '"summary"' in prompt:
            return {"text": '{"summary":"Caller booked a visit.","outcome":"booked"}', "tokens": 7}
        return {"text": '{"sentiment":"positive"}', "tokens": 3}
    context = ctx(tenant_a, owner_a)
    summary = await llm.summarize(db, context, "Can I book? Yes, confirmed. Thanks!", executor=executor)
    sentiment = await llm.classify_sentiment(db, context, "Thanks!", executor=executor)
    assert summary.value == {"summary": "Caller booked a visit.", "outcome": "booked"}
    assert sentiment.value == {"sentiment": "positive"}


@pytest.mark.live
async def test_opt_in_real_structured_provider(monkeypatch):
    if os.environ.get("VOXDESK_POST_CALL_LIVE") != "1" or not os.environ.get("VOXDESK_POST_CALL_LIVE_KEY"):
        pytest.skip("Requires explicit post-call live opt-in and provider credential")
    monkeypatch.setattr(settings, "openai_api_key", os.environ["VOXDESK_POST_CALL_LIVE_KEY"])
    model = os.environ.get("VOXDESK_POST_CALL_LIVE_MODEL", "gpt-4o-mini")
    result = await llm.StructuredExecutor(SCHEMA)(LLMChoice("openai", model, 300, "live"), 'Return JSON only: {"booked":true}', 15000)
    llm.Draft202012Validator(SCHEMA).validate(json.loads(result["text"]))
    assert json.loads(result["text"]) == {"booked": True}


async def test_gateway_fallback_uses_real_second_provider_contract(db, tenant_a, owner_a, configured):
    await save_policy(db, tenant_a.id, disabled_providers=["anthropic"])
    seen = []

    async def receiver(request):
        seen.append(request.url.host)
        if request.url.host == "api.openai.com":
            return httpx.Response(503)
        assert request.url.host == "generativelanguage.googleapis.com"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"booked":true}'}]}}], "usageMetadata": {"totalTokenCount": 11}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        output = await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "Booking confirmed", executor=llm.StructuredExecutor(SCHEMA, client=client))
    assert seen == ["api.openai.com", "generativelanguage.googleapis.com"]
    assert output.status == "completed" and output.invocations[0]["fallback_used"] is True
    assert output.invocations[0]["provider"] == "google"
    assert output.invocations[0]["tokens"] == 11


async def test_adapter_enforces_deadline_even_when_transport_ignores_timeout(configured):
    async def receiver(request):
        await asyncio.sleep(1)
        return openai_response('{"booked":true}')
    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        with pytest.raises(ProviderTimeoutError):
            await llm.StructuredExecutor(SCHEMA, client=client)(LLMChoice("openai", "gpt-test", 300, "test"), "JSON", 10)


async def test_response_size_is_bounded(configured):
    async def receiver(request):
        return httpx.Response(200, content=b"x" * (llm.MAX_RESPONSE_BYTES + 1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        with pytest.raises(ProviderInvalidRequestError, match="size limit"):
            await llm.StructuredExecutor(SCHEMA, client=client)(LLMChoice("openai", "gpt-test", 300, "test"), "JSON", 2000)


async def test_numeric_overflow_cannot_be_validated_as_a_number(db, tenant_a, owner_a, configured):
    schema = {"type": "object", "required": ["amount"], "properties": {"amount": {"type": "number"}}}

    async def executor(*args):
        return {"text": '{"amount":1e999}', "tokens": 1}

    result = await llm.extract_fields(db, ctx(tenant_a, owner_a), schema, "amount", executor=executor)
    assert result.status == "failed" and result.error == "invalid_output_schema"


async def test_usage_and_cost_do_not_cross_tenants(db, tenant_a, owner_a, tenant_b, configured):
    async def executor(*args):
        return {"text": '{"booked":true}', "tokens": 12}

    result = await llm.extract_fields(db, ctx(tenant_a, owner_a), SCHEMA, "Booked", executor=executor)
    await db.commit()
    assert result.status == "completed"
    assert not list((await db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_b.id))).all())
    owned = list((await db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_a.id))).all())
    assert len(owned) == 1
