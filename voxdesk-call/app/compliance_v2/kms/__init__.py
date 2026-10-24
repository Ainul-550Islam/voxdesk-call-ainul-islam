"""KMS provider factory.

The provider is chosen by ``KMS_PROVIDER``; each adapter is imported inside the
factory so a deployment that uses AWS never imports the Azure SDK and vice
versa. Construction is cheap and side-effect free apart from the development
provider, which materialises its local master key file on first use.
"""

from __future__ import annotations

from app.compliance_v2.kms.base import (
    DataKey,
    KeyHealth,
    KmsError,
    KmsNotConfigured,
    KmsPermanentError,
    KmsProvider,
    KmsUnavailable,
    encrypt_context,
)
from app.compliance_v2.kms.envelope import (
    EnvelopeAuthError,
    EnvelopeError,
    EnvelopeHeader,
    decrypt_legacy,
    decrypt_value,
    encrypt_value,
    is_envelope,
    is_legacy,
    needs_rewrap,
    parse,
    read_value,
    rotate_value,
)
from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings


def get_kms(settings: ComplianceV2Settings | None = None) -> KmsProvider:
    """Build the configured provider, or raise a typed error explaining why not."""
    cfg = settings or get_compliance_v2_settings()
    provider = cfg.kms_provider

    if provider == "aws":
        from app.compliance_v2.kms.aws_kms import AwsKms

        return AwsKms(
            key_id=cfg.kms_key_id,
            region=cfg.kms_region,
            max_attempts=cfg.kms_max_attempts,
        )
    if provider == "gcp":
        from app.compliance_v2.kms.gcp_kms import GcpKms

        return GcpKms(key_name=cfg.kms_key_id, max_attempts=cfg.kms_max_attempts)
    if provider == "azure":
        from app.compliance_v2.kms.azure_keyvault import AzureKeyVaultKms

        return AzureKeyVaultKms(
            vault_url=cfg.kms_azure_vault_url,
            key_name=cfg.kms_key_id,
            max_attempts=cfg.kms_max_attempts,
        )
    if provider == "local_dev":
        from app.compliance_v2.kms.local_dev import LocalDevKms

        return LocalDevKms(cfg.kms_local_master_key_path, environment=cfg.env)
    raise KmsNotConfigured(f"unknown KMS provider {provider!r}")


def provider_name(settings: ComplianceV2Settings | None = None) -> str:
    cfg = settings or get_compliance_v2_settings()
    return cfg.kms_provider


__all__ = [
    "DataKey",
    "EnvelopeAuthError",
    "EnvelopeError",
    "EnvelopeHeader",
    "KeyHealth",
    "KmsError",
    "KmsNotConfigured",
    "KmsPermanentError",
    "KmsProvider",
    "KmsUnavailable",
    "decrypt_legacy",
    "decrypt_value",
    "encrypt_context",
    "encrypt_value",
    "get_kms",
    "is_envelope",
    "is_legacy",
    "needs_rewrap",
    "parse",
    "provider_name",
    "read_value",
    "rotate_value",
]
