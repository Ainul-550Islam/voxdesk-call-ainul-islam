"""Provider boundary tests: remote success requires a real transport response."""
from __future__ import annotations

import inspect

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api import crm_writeback_routes as crm
from app.api import salesforce_routes as salesforce
from app.api.webhook_lifecycle_routes import WebhookCreate, _dispatch_webhook
from app.api.workflow_event_routes import OutcomeWritebackRequest, get_call_context, outcome_writeback
from app.auth.dependencies import TenantContext
from app.db.enterprise_models import CrmWritebackLog, SalesforceConnection, WebhookEndpoint
from app.webhooks.signing import verify
from tests.acd_support import live_call, production


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(204, "delivered"), (302, "dlq"), (401, "dlq"), (503, "failed")])
async def test_webhook_records_only_transport_observed_success(db, tenant_a, status, expected):
    endpoint = WebhookEndpoint(tenant_id=tenant_a.id, url="https://8.8.8.8/hooks",
                               secret="contract-only-test-secret", retry_policy={})
    db.add(endpoint)
    await db.commit()
    requests = []

    async def transport(request):
        requests.append(request)
        assert verify(endpoint.secret, request.content, request.headers["X-VoxDesk-Signature"])
        assert request.headers["X-VoxDesk-Event"]
        return httpx.Response(status, text="sensitive remote response must not be stored")

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=False) as client:
        result = await _dispatch_webhook(db, endpoint, "webhook.test", {"event": "contract"}, client=client)
    assert len(requests) == 1
    assert result.status == expected
    assert result.http_status == status
    assert result.attempts == 1
    assert (result.delivered_at is not None) == (status == 204)
    assert (endpoint.last_delivery_at is not None) == (status == 204)
    assert "sensitive" not in result.response_body
    assert (result.next_retry_at is not None) == (status == 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("url,active", [("https://127.0.0.1/private", True), ("https://8.8.8.8/hooks", False)])
async def test_unsafe_or_disabled_webhook_never_opens_transport(db, tenant_a, url, active):
    endpoint = WebhookEndpoint(tenant_id=tenant_a.id, url=url, is_active=active, retry_policy={})
    db.add(endpoint)
    await db.commit()

    def forbidden(request):
        raise AssertionError("unsafe/disabled endpoint reached transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        result = await _dispatch_webhook(db, endpoint, "webhook.test", {}, client=client)
    assert result.status == "dlq"
    assert result.http_status == 0
    assert result.attempts == 0
    assert result.delivered_at is None


def test_webhook_configuration_rejects_private_destination():
    with pytest.raises(ValueError):
        WebhookCreate(url="https://127.0.0.1/private")


UNIMPLEMENTED = [
    salesforce.connect_salesforce, salesforce.oauth_authorize, salesforce.oauth_callback,
    salesforce.refresh_token, salesforce.salesforce_writeback, salesforce.sync_salesforce,
    salesforce.api_limits, salesforce.describe_objects, salesforce.bulk_writeback,
    crm.writeback_disposition, crm.writeback_task_note, crm.writeback_extracted_fields,
    crm.create_mapping, crm.list_mappings, crm.backfill_crm,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", UNIMPLEMENTED, ids=lambda fn: fn.__name__)
async def test_unimplemented_remote_operations_do_not_fabricate_completion(db, tenant_a, owner_a, operation):
    ctx = TenantContext(user=owner_a, tenant=tenant_a)
    kwargs = {name: None for name, parameter in inspect.signature(operation).parameters.items()
              if parameter.default is inspect.Parameter.empty}
    kwargs.update(ctx=ctx, session=db)
    with pytest.raises(HTTPException) as caught:
        await operation(**kwargs)
    assert caught.value.status_code == 501
    assert "NOT_IMPLEMENTED" in str(caught.value.detail)
    for model in (SalesforceConnection, CrmWritebackLog):
        assert (await db.scalar(select(func.count()).select_from(model))) == 0


@pytest.mark.asyncio
async def test_workflow_preserves_local_context_but_does_not_fake_remote_writeback(db, tenant_a, owner_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    ctx = TenantContext(user=owner_a, tenant=tenant_a)
    context = await get_call_context(call.id, ctx, db)
    assert context.call_id == str(call.id)
    assert context.bookings == []
    with pytest.raises(HTTPException) as caught:
        await outcome_writeback(call.id, OutcomeWritebackRequest(target="crm"), ctx, db)
    assert caught.value.status_code == 501
    assert (await db.scalar(select(func.count()).select_from(CrmWritebackLog))) == 0
