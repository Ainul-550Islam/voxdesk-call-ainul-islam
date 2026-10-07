"""Real media-token signing must fail closed without the configured secret."""
import ast
from pathlib import Path
from uuid import uuid4
import pytest
from app.core.config import settings, Settings
from app.telephony.realtime import ConfigurationError, issue_media_stream_token, verify_media_stream_token


@pytest.mark.parametrize('secret', ['', '   '])
def test_unconfigured_media_signing_cannot_issue_or_verify(monkeypatch, secret):
    monkeypatch.setattr(settings, 'jwt_secret', secret)
    ids = {'call_id': uuid4(), 'tenant_id': uuid4()}
    with pytest.raises(ConfigurationError):
        issue_media_stream_token(**ids)
    with pytest.raises(ConfigurationError):
        verify_media_stream_token('9999999999.signature', **ids)


def test_signature_binds_call_tenant_and_key(monkeypatch):
    monkeypatch.setattr(settings, 'jwt_secret', 'contract-test-key-that-is-not-a-provider-secret')
    ids = {'call_id': uuid4(), 'tenant_id': uuid4()}
    token = issue_media_stream_token(**ids)
    assert verify_media_stream_token(token, **ids)
    assert not verify_media_stream_token(token, call_id=uuid4(), tenant_id=ids['tenant_id'])
    assert not verify_media_stream_token(token, call_id=ids['call_id'], tenant_id=uuid4())
    monkeypatch.setattr(settings, 'jwt_secret', 'different-contract-key')
    assert not verify_media_stream_token(token, **ids)


def test_production_validation_rejects_default_identity_secrets():
    config = Settings(_env_file=None, app_env='production', jwt_secret='insecure-development-only-change-me', secret_key='insecure-development-only-change-me')
    issues = config.validate_security()
    assert any('JWT_SECRET' in issue for issue in issues)
    assert any('SECRET_KEY' in issue for issue in issues)


def test_no_constant_secret_fallback_in_any_production_module():
    findings = []
    for path in Path('app').rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                identifiers = [n.attr.lower() for n in ast.walk(node) if isinstance(n, ast.Attribute)] + [n.id.lower() for n in ast.walk(node) if isinstance(n, ast.Name)]
                is_secret = any('secret' in name or name in {'jwt_key', 'signing_key', 'encryption_key'} for name in identifiers)
                if is_secret and any(isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.strip() for v in node.values[1:]):
                    findings.append(f'{path}:{node.lineno}')
    assert findings == []
