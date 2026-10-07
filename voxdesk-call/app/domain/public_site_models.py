"""Prompt 5: Public Website, Public Catalog, Contact Sales, and Auth Boundary domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import AppError


class PublicDomainError(AppError):
    """Domain error with explicit HTTP status_code and machine-readable code."""

    def __init__(
        self,
        message: str = "Public boundary error",
        *,
        status_code: int = 400,
        code: str = "public_boundary_error",
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        super().__init__(message, code=code, detail=detail, headers=headers)


class PublicRouteCategory(str, Enum):
    PUBLIC = "public"
    AUTH = "auth"
    PROTECTED = "protected"
    PUBLIC_WIDGET = "public_widget"


class PublicNavItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    href: str
    category: str = "public"
    requires_auth: bool = False


class PublicPricingTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    name: str
    catalogue_source: Literal["DATABASE", "SEED_DEFAULT"] = "DATABASE"
    description: str = ""
    monthly_price_cents: int
    annual_price_cents: int | None = None
    currency: str = "usd"
    included_minutes: int = 0
    included_sms_segments: int = 0
    included_llm_tokens: int = 0
    included_tts_characters: int = 0
    max_concurrency: int | None = None
    overage_enabled: bool = False
    trial_days: int = 0
    features: list[str] = Field(default_factory=list)
    cta_label: str = "Start Building"
    cta_href: str = "/signup"
    is_enterprise: bool = False


class PublicStatusComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    status: str
    description: str
    updated_at: datetime


class PublicSiteStatusSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_status: str
    components: list[PublicStatusComponent] = Field(default_factory=list)
    checked_at: datetime
    message: str


class PublicSEOMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    canonical_url: str
    robots: str = "index, follow"
    og_type: str = "website"


class PublicSiteManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_name: str = "VoxDesk"
    tagline: str = "Enterprise Voice & Chat AI Agent Platform"
    canonical_origin: str = "https://voxdesk.ai"
    public_routes: list[str] = Field(default_factory=list)
    protected_route_prefixes: list[str] = Field(default_factory=list)
    navigation: list[PublicNavItem] = Field(default_factory=list)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    use_cases: list[dict[str, Any]] = Field(default_factory=list)
    security_highlights: list[dict[str, Any]] = Field(default_factory=list)
    pricing_tiers: list[PublicPricingTier] = Field(default_factory=list)
    status_summary: PublicSiteStatusSummary
    seo_meta: PublicSEOMeta


class PublicContactSalesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=200)
    work_email: str = Field(..., min_length=5, max_length=254)
    company_name: str = Field(..., min_length=2, max_length=200)
    job_title: str = Field(default="", max_length=160)
    phone_number: str | None = Field(default=None, max_length=40)
    monthly_call_volume: str = Field(default="10k-50k", max_length=64)
    primary_use_case: str = Field(default="voice_agents", max_length=120)
    message: str = Field(default="", max_length=4000)
    source_path: str = Field(default="/contact-sales", max_length=255)

    @field_validator("work_email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or "." not in cleaned.split("@", 1)[-1]:
            raise ValueError("A valid work email address is required.")
        return cleaned

    @field_validator("full_name", "company_name")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Field must contain at least 2 non-whitespace characters.")
        return cleaned


class PublicContactSalesRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    full_name: str
    work_email: str
    company_name: str
    job_title: str
    phone_number: str | None = None
    monthly_call_volume: str
    primary_use_case: str
    status: str
    created_at: datetime
    confirmation_message: str


class PublicSignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(..., min_length=2, max_length=160)
    full_name: str = Field(default="", max_length=200)
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=12, max_length=200)
    industry: str = Field(default="technology", max_length=80)
    next_path: str | None = Field(default=None, max_length=512)


class PublicAuthBoundaryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    classification: PublicRouteCategory
    requires_auth: bool
    is_safe_next_path: bool = True
    sanitized_next_path: str = "/dashboard"
    redirect_to: str | None = None
    cache_control: str = "public, max-age=60"
    reason: str = "ok"
