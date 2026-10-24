"""Typed environment configuration for Integrations v2 OAuth apps, sync engine, and vendor endpoints.

Secrets never have hardcoded non-empty defaults; missing credentials cause
``is_oauth_app_configured(provider)`` to return ``False`` and OAuth initiation
to fail closed with an operator-actionable diagnostic. If an operator explicitly
enables a provider via ``<PROVIDER>_ENABLED=true`` without supplying its secret,
validation fails closed at startup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ZohoRegion = Literal["us", "eu", "in", "au", "jp", "ca"]

ZOHO_ACCOUNTS_HOSTS: dict[str, str] = {
    "us": "accounts.zoho.com",
    "eu": "accounts.zoho.eu",
    "in": "accounts.zoho.in",
    "au": "accounts.zoho.com.au",
    "jp": "accounts.zoho.jp",
    "ca": "accounts.zohocloud.ca",
}

ZOHO_CRM_API_HOSTS: dict[str, str] = {
    "us": "www.zohoapis.com",
    "eu": "www.zohoapis.eu",
    "in": "www.zohoapis.in",
    "au": "www.zohoapis.com.au",
    "jp": "www.zohoapis.jp",
    "ca": "www.zohoapis.ca",
}

ZOHO_DESK_API_HOSTS: dict[str, str] = {
    "us": "desk.zoho.com",
    "eu": "desk.zoho.eu",
    "in": "desk.zoho.in",
    "au": "desk.zoho.com.au",
    "jp": "desk.zoho.jp",
    "ca": "desk.zohocloud.ca",
}


class IntegrationsV2Settings(BaseSettings):
    """Environment-backed OAuth app, sync pagination, and HTTP configuration for all 11 v2 vendors."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # Zendesk
    zendesk_enabled: bool = Field(default=False, validation_alias="ZENDESK_ENABLED")
    zendesk_client_id: str = Field(default="", validation_alias="ZENDESK_CLIENT_ID")
    zendesk_client_secret: str = Field(default="", validation_alias="ZENDESK_CLIENT_SECRET")
    zendesk_redirect_uri: str = Field(default="", validation_alias="ZENDESK_REDIRECT_URI")
    zendesk_webhook_secret: str = Field(default="", validation_alias="ZENDESK_WEBHOOK_SECRET")

    # ServiceNow
    servicenow_enabled: bool = Field(default=False, validation_alias="SERVICENOW_ENABLED")
    servicenow_client_id: str = Field(default="", validation_alias="SERVICENOW_CLIENT_ID")
    servicenow_client_secret: str = Field(
        default="", validation_alias="SERVICENOW_CLIENT_SECRET"
    )
    servicenow_redirect_uri: str = Field(default="", validation_alias="SERVICENOW_REDIRECT_URI")

    # Freshdesk (per-tenant API key; optional global test domain + key)
    freshdesk_enabled: bool = Field(default=False, validation_alias="FRESHDESK_ENABLED")
    freshdesk_default_domain: str = Field(default="", validation_alias="FRESHDESK_DOMAIN")
    freshdesk_api_key: str = Field(default="", validation_alias="FRESHDESK_API_KEY")

    # Zoho (shared OAuth app across Zoho Desk + Zoho CRM, region-aware)
    zoho_enabled: bool = Field(default=False, validation_alias="ZOHO_ENABLED")
    zoho_client_id: str = Field(default="", validation_alias="ZOHO_CLIENT_ID")
    zoho_client_secret: str = Field(default="", validation_alias="ZOHO_CLIENT_SECRET")
    zoho_redirect_uri: str = Field(default="", validation_alias="ZOHO_REDIRECT_URI")
    zoho_region: str = Field(default="us", validation_alias="ZOHO_REGION")

    # Freshsales Suite
    freshsales_enabled: bool = Field(default=False, validation_alias="FRESHSALES_ENABLED")
    freshsales_client_id: str = Field(default="", validation_alias="FRESHSALES_CLIENT_ID")
    freshsales_client_secret: str = Field(
        default="", validation_alias="FRESHSALES_CLIENT_SECRET"
    )
    freshsales_redirect_uri: str = Field(default="", validation_alias="FRESHSALES_REDIRECT_URI")

    # Microsoft Dynamics 365 (Entra ID v2.0 + Dataverse resource)
    dynamics_enabled: bool = Field(default=False, validation_alias="DYNAMICS_ENABLED")
    dynamics_client_id: str = Field(default="", validation_alias="DYNAMICS_CLIENT_ID")
    dynamics_client_secret: str = Field(default="", validation_alias="DYNAMICS_CLIENT_SECRET")
    dynamics_tenant_id: str = Field(default="common", validation_alias="DYNAMICS_TENANT_ID")
    dynamics_resource: str = Field(default="", validation_alias="DYNAMICS_RESOURCE")
    dynamics_redirect_uri: str = Field(default="", validation_alias="DYNAMICS_REDIRECT_URI")

    # Pipedrive v2
    pipedrive_enabled: bool = Field(default=False, validation_alias="PIPEDRIVE_ENABLED")
    pipedrive_client_id: str = Field(default="", validation_alias="PIPEDRIVE_CLIENT_ID")
    pipedrive_client_secret: str = Field(
        default="", validation_alias="PIPEDRIVE_CLIENT_SECRET"
    )
    pipedrive_redirect_uri: str = Field(default="", validation_alias="PIPEDRIVE_REDIRECT_URI")

    # Microsoft Teams (Azure AD / Graph v1.0)
    teams_enabled: bool = Field(default=False, validation_alias="TEAMS_ENABLED")
    teams_client_id: str = Field(default="", validation_alias="TEAMS_CLIENT_ID")
    teams_client_secret: str = Field(default="", validation_alias="TEAMS_CLIENT_SECRET")
    teams_tenant_id: str = Field(default="common", validation_alias="TEAMS_TENANT_ID")
    teams_redirect_uri: str = Field(default="", validation_alias="TEAMS_REDIRECT_URI")
    teams_bot_app_id: str = Field(default="", validation_alias="TEAMS_BOT_APP_ID")

    # Zoom
    zoom_enabled: bool = Field(default=False, validation_alias="ZOOM_ENABLED")
    zoom_client_id: str = Field(default="", validation_alias="ZOOM_CLIENT_ID")
    zoom_client_secret: str = Field(default="", validation_alias="ZOOM_CLIENT_SECRET")
    zoom_account_id: str = Field(default="", validation_alias="ZOOM_ACCOUNT_ID")
    zoom_redirect_uri: str = Field(default="", validation_alias="ZOOM_REDIRECT_URI")
    zoom_webhook_secret_token: str = Field(
        default="", validation_alias="ZOOM_WEBHOOK_SECRET_TOKEN"
    )

    # Slack
    slack_enabled: bool = Field(default=False, validation_alias="SLACK_ENABLED")
    slack_client_id: str = Field(default="", validation_alias="SLACK_CLIENT_ID")
    slack_client_secret: str = Field(default="", validation_alias="SLACK_CLIENT_SECRET")
    slack_signing_secret: str = Field(default="", validation_alias="SLACK_SIGNING_SECRET")
    slack_redirect_uri: str = Field(default="", validation_alias="SLACK_REDIRECT_URI")

    # Sync engine & HTTP client tuning
    sync_page_size: int = Field(
        default=100,
        ge=1,
        le=500,
        validation_alias=AliasChoices("SYNC_PAGE_SIZE", "INT2_SYNC_PAGE_SIZE"),
    )
    sync_max_pages_per_run: int = Field(
        default=25,
        ge=1,
        le=500,
        validation_alias=AliasChoices("SYNC_MAX_PAGES_PER_RUN", "INT2_SYNC_MAX_PAGES_PER_RUN"),
    )
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0.1,
        le=120.0,
        validation_alias=AliasChoices("HTTP_TIMEOUT_S", "INT2_HTTP_TIMEOUT_S"),
    )
    http_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        validation_alias=AliasChoices("MAX_RETRIES", "INT2_HTTP_MAX_RETRIES"),
    )
    agent_tool_max_timeout_ms: int = Field(
        default=3000,
        ge=200,
        le=15000,
        validation_alias="INT2_AGENT_TOOL_MAX_TIMEOUT_MS",
    )

    @model_validator(mode="after")
    def _refuse_empty_secrets_when_enabled(self) -> "IntegrationsV2Settings":
        enabled_checks: list[tuple[bool, str, str, str]] = [
            (
                self.zendesk_enabled,
                "zendesk",
                self.zendesk_client_id,
                self.zendesk_client_secret,
            ),
            (
                self.servicenow_enabled,
                "servicenow",
                self.servicenow_client_id,
                self.servicenow_client_secret,
            ),
            (
                self.freshdesk_enabled,
                "freshdesk",
                self.freshdesk_default_domain,
                self.freshdesk_api_key,
            ),
            (self.zoho_enabled, "zoho", self.zoho_client_id, self.zoho_client_secret),
            (
                self.freshsales_enabled,
                "freshsales",
                self.freshsales_client_id,
                self.freshsales_client_secret,
            ),
            (
                self.dynamics_enabled,
                "dynamics365",
                self.dynamics_client_id,
                self.dynamics_client_secret,
            ),
            (
                self.pipedrive_enabled,
                "pipedrive",
                self.pipedrive_client_id,
                self.pipedrive_client_secret,
            ),
            (self.teams_enabled, "teams", self.teams_client_id, self.teams_client_secret),
            (self.zoom_enabled, "zoom", self.zoom_client_id, self.zoom_client_secret),
            (self.slack_enabled, "slack", self.slack_client_id, self.slack_client_secret),
        ]
        for enabled, name, cid, csec in enabled_checks:
            if enabled and (not cid.strip() or not csec.strip()):
                raise ValueError(
                    f"Provider '{name}' is enabled via environment flag but required credentials/secrets are empty"
                )
        return self

    def oauth_credentials(self, provider: str) -> tuple[str, str, str]:
        """Return ``(client_id, client_secret, redirect_uri)`` for an OAuth2 provider."""
        mapping: dict[str, tuple[str, str, str]] = {
            "zendesk": (
                self.zendesk_client_id,
                self.zendesk_client_secret,
                self.zendesk_redirect_uri,
            ),
            "servicenow": (
                self.servicenow_client_id,
                self.servicenow_client_secret,
                self.servicenow_redirect_uri,
            ),
            "zoho_desk": (
                self.zoho_client_id,
                self.zoho_client_secret,
                self.zoho_redirect_uri,
            ),
            "zoho_crm": (
                self.zoho_client_id,
                self.zoho_client_secret,
                self.zoho_redirect_uri,
            ),
            "freshsales": (
                self.freshsales_client_id,
                self.freshsales_client_secret,
                self.freshsales_redirect_uri,
            ),
            "dynamics365": (
                self.dynamics_client_id,
                self.dynamics_client_secret,
                self.dynamics_redirect_uri,
            ),
            "pipedrive": (
                self.pipedrive_client_id,
                self.pipedrive_client_secret,
                self.pipedrive_redirect_uri,
            ),
            "teams": (
                self.teams_client_id,
                self.teams_client_secret,
                self.teams_redirect_uri,
            ),
            "zoom": (
                self.zoom_client_id,
                self.zoom_client_secret,
                self.zoom_redirect_uri,
            ),
            "slack": (
                self.slack_client_id,
                self.slack_client_secret,
                self.slack_redirect_uri,
            ),
        }
        return mapping.get(provider, ("", "", ""))

    def is_oauth_app_configured(self, provider: str) -> bool:
        cid, csec, _ = self.oauth_credentials(provider)
        return bool(cid.strip() and csec.strip())


@lru_cache(maxsize=1)
def get_int2_settings() -> IntegrationsV2Settings:
    return IntegrationsV2Settings()


def reset_int2_settings_cache() -> None:
    get_int2_settings.cache_clear()
