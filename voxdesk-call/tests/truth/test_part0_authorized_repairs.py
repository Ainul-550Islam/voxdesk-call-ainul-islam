"""Regression contracts for the authorized PART 0 clean-up, with real DB boundaries."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from app.compliance.qms_adapters import QMSContext, QMSCapabilityUnavailable, get_qms_adapter
from app.db.models import Agent, AuditLog
from app.db.enterprise_models import DncEntry
from app.api import call_search_export_routes as calls
from tests.conftest import auth_headers


@pytest.mark.parametrize('provider', ['veeva_vault', 'mastercontrol', 'etq'])
@pytest.mark.parametrize('configured', [False, True])
async def test_qms_never_confuses_credentials_with_connection(provider, configured):
    credentials = dict(instance_url='https://example.com', base_url='https://example.com', username='operator', password='secret-value', api_key='secret-value') if configured else {}
    context = QMSContext(uuid4(), uuid4(), uuid4(), provider, {}, credentials)
    adapter = get_qms_adapter(context)
    health = await adapter.health_check()
    assert not health.connected
    assert health.details['request_attempted'] is False
    assert health.details['state'] == ('UNSUPPORTED_CAPABILITY' if configured else 'NOT_CONFIGURED')
    assert 'secret-value' not in str(health)
    for method, args in [('list_documents', ()), ('get_document', ('doc',)), ('create_finding', (None,)), ('get_traceability', ('document', 'doc')), ('assemble_audit_package', ('framework',))]:
        with pytest.raises(QMSCapabilityUnavailable) as caught:
            await getattr(adapter, method)(*args)
        assert caught.value.status_code == 501
        assert caught.value.detail['operation'] == method
        assert caught.value.code == health.details['state']


async def test_legacy_test_creation_is_scoped_and_cannot_issue_fake_session(db, client, tenant_a, tenant_b, owner_a, owner_b):
    agent = Agent(tenant_id=tenant_a.id, name='Scoped', external_key='truth-legacy')
    db.add(agent)
    await db.commit()
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)
    path = f'/api/v1/agents/{agent.id}/test'
    denied = await client.post(path, headers=headers_b, json={})
    assert denied.status_code == 404
    own = await client.post(path, headers=headers_a, json={})
    assert own.status_code == 501
    assert 'token' not in own.json()
    for headers in [headers_a, headers_b]:
        for suffix in ['', '/health']:
            assert (await client.get('/api/v1/agent-tests/nonexistent'+suffix, headers=headers)).status_code == 404
    assert (await client.get('/api/v1/agent-tests/nonexistent/health')).status_code == 401


async def test_dnc_and_audit_commit_together(db, tenant_a, owner_a):
    ctx = SimpleNamespace(tenant_id=tenant_a.id, user_id=owner_a.id)
    result = await calls.add_dnc_entry(calls.DncAddRequest(phone='+14155552671', reason='requested', source='manual'), ctx, db)
    entry = await db.scalar(select(DncEntry).where(DncEntry.id == __import__('uuid').UUID(result['id'])))
    event = await db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant_a.id, AuditLog.event_type == 'dnc.added'))
    assert entry is not None and event is not None
    assert event.actor_user_id == owner_a.id
    assert '+14155552671' not in str(event.detail)


async def test_failed_audit_cannot_commit_dnc(sessionmaker_, tenant_a, owner_a, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError('audit storage unavailable')
    monkeypatch.setattr(calls, 'record_event', unavailable)
    ctx = SimpleNamespace(tenant_id=tenant_a.id, user_id=owner_a.id)
    with pytest.raises(RuntimeError, match='audit storage unavailable'):
        async with sessionmaker_() as session:
            await calls.add_dnc_entry(calls.DncAddRequest(phone='+14155552672', reason='requested', source='manual'), ctx, session)
    async with sessionmaker_() as verify:
        assert await verify.scalar(select(func.count()).select_from(DncEntry).where(DncEntry.phone == '+14155552672')) == 0


def test_export_defaults_are_stable():
    assert calls.CallExportRequest().fields == sorted(calls.ALLOWED_EXPORT_FIELDS)


def test_unreferenced_template_extensions_are_not_mounted():
    from app.main import app
    removed_modules = {'app.api.'+name+'_routes' for name in ['knowledge_base', 'pcap', 'phone_number_lifecycle', 'recording_management', 'tool_registry', 'workflow_event']}
    assert not [r.path for r in app.routes if getattr(getattr(r, 'endpoint', None), '__module__', '') in removed_modules and '/extended/' in r.path]


async def test_qms_http_unavailable_is_not_empty_success_and_is_tenant_scoped(db, client, tenant_a, tenant_b, owner_a, owner_b):
    from app.db.models import Environment
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant_a.id, Environment.kind == 'production'))
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)
    path = f'/api/compliance/qms/veeva_vault/documents?environment_id={environment.id}'
    response = await client.get(path, headers=headers_a)
    assert response.status_code == 501
    assert 'documents' not in response.json()
    denied = await client.get(path, headers=headers_b)
    assert denied.status_code == 404


async def test_replay_does_not_manufacture_a_signed_recording_link(db, tenant_a, owner_a):
    from tests.conftest import make_call
    call = await make_call(db, tenant_a)
    ctx = SimpleNamespace(tenant_id=tenant_a.id, user_id=owner_a.id)
    replay = await calls.get_call_replay(call.id, include_transcript=False, include_timeline=False, ctx=ctx, session=db)
    assert replay.recording_url is None
