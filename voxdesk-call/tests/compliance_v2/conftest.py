"""Shared fixtures for the Batch 03 compliance tests.

Everything here points the compliance controls at a throwaway directory and a
development KMS, so no test writes to ``var/compliance`` or reaches a cloud
provider. The environment is applied *before* the settings instance is
constructed, and the cached instance is dropped on the way out, because a stale
settings object would silently use the previous test's paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from app.compliance_v2.settings import reset_compliance_v2_settings


@pytest.fixture
def compliance_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A development-mode compliance configuration rooted in ``tmp_path``."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COMPLIANCE_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("KMS_PROVIDER", "local_dev")
    monkeypatch.setenv("KMS_LOCAL_MASTER_KEY_PATH", str(tmp_path / "local_master.key"))
    monkeypatch.setenv("AUDIT_SIGNING_KEY_PATH", str(tmp_path / "audit_signing.pem"))
    monkeypatch.setenv("DATA_REGIONS", "us-east-1,eu-west-1")
    monkeypatch.setenv("DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("SIEM_ENABLED", "true")
    reset_compliance_v2_settings()
    yield evidence
    reset_compliance_v2_settings()


@pytest.fixture
def compliance_settings(compliance_env: Path):
    from app.compliance_v2.settings import get_compliance_v2_settings

    return get_compliance_v2_settings()


@pytest.fixture
def local_kms(compliance_settings):
    """The development KMS adapter bound to this test's master key file."""
    from app.compliance_v2.kms.local_dev import LocalDevKms

    return LocalDevKms(
        path=compliance_settings.kms_local_master_key_path,
        environment=compliance_settings.env,
    )


@pytest.fixture
def tenant_scope(tenant_a):
    """The ``tenant:<id>`` chain scope used by the governance ledger."""
    return f"tenant:{tenant_a.id}"


@pytest_asyncio.fixture
async def governance_scope(db, tenant_a):
    """A real ``GovernanceScope`` for ``tenant_a``, as the evidence API expects.

    The tenant insert hook creates the parent organization, so this only has to
    load it; nothing here fabricates an id.
    """
    from app.db.models import Organization
    from app.governance.context import GovernanceScope

    organization = await db.get(Organization, tenant_a.organization_id)
    assert organization is not None, "the tenant insert hook should have created one"
    return GovernanceScope(tenant=tenant_a, organization=organization)
