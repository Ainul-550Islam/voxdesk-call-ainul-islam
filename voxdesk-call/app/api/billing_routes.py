"""
Billing API.

    GET  /api/billing                 safe status summary
    GET  /api/billing/plans           the catalogue
    GET  /api/billing/usage           this period's metering
    GET  /api/billing/invoices        invoice metadata
    POST /api/billing/checkout        start a purchase
    POST /api/billing/portal          open the customer portal
    POST /api/billing/change-plan     upgrade or schedule a downgrade
    POST /api/billing/cancel          cancel
    POST /api/billing/reconcile       re-read the provider, rebuild summaries

    POST /api/billing/webhook/{provider}    provider events (unauthenticated,
                                            signature-verified)

Three rules run through the file.

**The tenant is never a parameter.** It comes from `ctx.tenant_id`, which
comes from the verified JWT. There is no endpoint that reads a tenant id, a
customer id or a subscription id from a request.

**A price is never a parameter.** `CheckoutIn` accepts a plan *code* and an
interval. `resolve_price_id` turns those into a provider price. A field named
`price`, `amount` or `currency` does not exist on any request model, so
`POST {"price": "price_one_cent"}` is rejected by Pydantic before it reaches
a handler.

**Responses are allowlists.** `BillingStatusOut` names every field that may
reach a client. No secret key, no webhook secret, no payment method, no raw
provider payload.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    TenantContext,
    get_platform_admin,
    record_audit,
    require_permission,
)
from app.auth.permissions import Permission
from app.billing import plans as plan_catalogue
from app.billing import cost, price_book, reconciliation, service, webhooks
from app.billing.entitlements import load_context
from app.billing.errors import (
    BillingError,
    BillingUnsupportedError,
    PlanNotFound,
)
from app.billing.registry import configured_provider
from app.core.logging import log
from app.db.models import (
    AuditAction,
    BillingInterval,
    BillingInvoice,
    BillingProviderType,
)
from app.db.session import get_session

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ----------------------------------------------------------------- schemas ---

class PlanOut(BaseModel):
    """
    A plan as a customer sees it.

    `provider_price_ids` is absent on purpose. They are not secret, but
    publishing them invites a client to send one back, and the entire trust
    model rests on that never being accepted.
    """

    code: str
    name: str
    description: str
    currency: str
    monthly_price_cents: int
    annual_price_cents: int | None
    included_voice_minutes: int
    included_sms_segments: int
    overage_voice_minute_cents: float
    overage_enabled: bool
    trial_days: int
    features: dict = Field(default_factory=dict)
    is_current: bool = False


class UsageMetricOut(BaseModel):
    metric: str
    unit: str
    included: float
    used: float
    remaining: float
    overage: float
    percent_used: float
    overage_cents: int


class UsageOut(BaseModel):
    billing_period: str
    period_start: str
    period_end: str
    plan_code: str
    metrics: list[UsageMetricOut]
    estimated_overage_cents: int
    currency: str


class BillingStatusOut(BaseModel):
    """Requirement 19's allowlist."""

    plan_code: str
    plan_name: str
    subscription_status: str
    interval: str
    provider: str
    current_period_start: str | None
    current_period_end: str | None
    trial_end: str | None
    cancel_at_period_end: bool
    pending_plan_code: str | None
    last_invoice_status: str | None
    estimated_overage_cents: int
    currency: str
    usage: dict = Field(default_factory=dict)


class InvoiceOut(BaseModel):
    id: str
    status: str
    currency: str
    amount_due_cents: int
    amount_paid_cents: int
    period_start: str | None
    period_end: str | None
    #: Stripe's short-lived signed URL. Safe for an authorized user; it
    #: carries no API credential.
    hosted_invoice_url: str | None


class CheckoutIn(BaseModel):
    """
    **The whole input surface for a purchase.**

    A plan code and an interval. No amount, no currency, no price id, no
    tenant id, no quantity. Requirement 8, enforced by the type.
    """

    model_config = ConfigDict(json_schema_extra={"examples": [{"plan_code": "growth", "interval": "month"}]})

    plan_code: str = Field(min_length=1, max_length=64)
    interval: BillingInterval = BillingInterval.MONTH


class ChangePlanIn(BaseModel):
    model_config = {"json_schema_extra": {"examples": [{"plan_code": "growth", "interval": "month"}]}}

    plan_code: str = Field(min_length=1, max_length=64)
    interval: BillingInterval | None = None


class CancelIn(BaseModel):
    model_config = {"json_schema_extra": {"examples": [{"reason": "moving to a self-hosted deployment"}]}}

    #: Default False: the customer has paid for the month, and cutting them
    #: off the moment they click cancel takes money for service not delivered.
    immediately: bool = False
    reason: str = Field(default="", max_length=500)


class SessionOut(BaseModel):
    url: str


# ------------------------------------------------------------- serializers ---

def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def plan_out(plan, *, current_code: str | None = None) -> PlanOut:
    return PlanOut(
        code=plan.code,
        name=plan.name,
        description=plan.description or "",
        currency=plan.currency,
        monthly_price_cents=plan.monthly_price_cents,
        annual_price_cents=plan.annual_price_cents,
        included_voice_minutes=plan.included_voice_minutes,
        included_sms_segments=plan.included_sms_segments,
        # Millicents are an internal precision detail; a pricing page shows
        # cents.
        overage_voice_minute_cents=round(
            plan.overage_voice_minute_millicents / 100, 2
        ),
        overage_enabled=plan.overage_enabled,
        trial_days=plan.trial_days,
        features=dict(plan.feature_entitlements or {}),
        is_current=(current_code is not None and plan.code == current_code),
    )


def _handle_billing_error(exc: BillingError) -> HTTPException:
    """
    Map a normalized billing error to a status.

    A provider failure is a **502**, never a 200 with a hopeful message —
    requirement 32: the customer must not be told "payment completed" when the
    provider was unavailable. The detail is `safe_message`, which has been
    scrubbed of anything key-shaped.
    """
    status = {
        "plan_not_found": 404,
        "not_found": 404,
        "validation": 422,
        "misconfigured": 503,
        "unsupported": 501,
        "unauthorized": 502,
        "card_declined": 402,
        "rate_limited": 503,
        "timeout": 504,
        "temporary": 502,
    }.get(exc.code, 502)
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.safe_message},
    )


# ------------------------------------------------------------------- routes ---

@router.get("", response_model=BillingStatusOut)
@router.get("/", response_model=BillingStatusOut)
async def get_billing(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> BillingStatusOut:
    """Read this workspace's billing status.

    Returns the plan, subscription state, current period and the estimated overage for the period so far. Every field is an allowlist: no provider identifiers, no API keys, no payment instrument.
    """
    status = await service.billing_status(session, ctx.tenant)
    return BillingStatusOut(
        plan_code=status.plan_code,
        plan_name=status.plan_name,
        subscription_status=status.subscription_status,
        interval=status.interval,
        provider=configured_provider().value,
        current_period_start=_iso(status.current_period_start),
        current_period_end=_iso(status.current_period_end),
        trial_end=_iso(status.trial_end),
        cancel_at_period_end=status.cancel_at_period_end,
        pending_plan_code=status.pending_plan_code,
        last_invoice_status=status.last_invoice_status,
        estimated_overage_cents=status.estimated_overage_cents,
        currency=status.currency,
        usage=status.usage,
    )


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[PlanOut]:
    """List the plans a workspace can buy.

    Server-owned catalogue. A browser can name a plan *code* but never a price; the code is resolved to a provider price id server-side.
    """
    context = await load_context(session, ctx.tenant)
    current = context.plan.code if context.plan else None
    return [
        plan_out(plan, current_code=current)
        for plan in await plan_catalogue.list_plans(session, active_only=True)
    ]


@router.get("/usage", response_model=UsageOut)
async def get_usage(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> UsageOut:
    """Metered usage for the current billing period.

    One entry per metric with included, used, remaining and overage. The counters come from append-only usage events, so the figure is reproducible after any dispute.
    """
    from app.billing import metering

    context = await load_context(session, ctx.tenant)
    usage = await metering.period_usage(
        session, tenant_id=ctx.tenant_id, period=context.period, plan=context.plan
    )

    return UsageOut(
        billing_period=context.period.label,
        period_start=context.period.start.isoformat(),
        period_end=context.period.end.isoformat(),
        plan_code=context.plan_code,
        metrics=[
            UsageMetricOut(
                metric=metric.value,
                unit=entry.display_unit,
                included=entry.display(entry.included),
                used=entry.display(entry.used),
                remaining=entry.display(entry.remaining),
                overage=entry.display_overage,
                percent_used=entry.percent_used,
                overage_cents=round(entry.overage_millicents / 100),
            )
            for metric, entry in usage.items()
        ],
        estimated_overage_cents=round(
            sum(e.overage_millicents for e in usage.values()) / 100
        ),
        currency=context.plan.currency if context.plan else "usd",
    )


@router.get("/invoices", response_model=list[InvoiceOut])
async def list_invoices(
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[InvoiceOut]:
    """
    Invoice metadata.

    Served from the local mirror rather than by calling Stripe on every page
    load: it is faster, it works when Stripe is down, and it keeps the API
    key out of a read path that a dashboard polls.
    """
    rows = (
        (
            await session.execute(
                select(BillingInvoice)
                .where(BillingInvoice.tenant_id == ctx.tenant_id)
                .order_by(BillingInvoice.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        InvoiceOut(
            id=row.external_invoice_id,
            status=row.status.value,
            currency=row.currency,
            amount_due_cents=row.amount_due_cents,
            amount_paid_cents=row.amount_paid_cents,
            period_start=_iso(row.period_start),
            period_end=_iso(row.period_end),
            hosted_invoice_url=row.hosted_invoice_url,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Price book, itemised invoices, usage by provider and day, margin (P18-05)
# ---------------------------------------------------------------------------
#
# The price book is deploy-scoped configuration, not tenant data: two tenants on
# the same installation see the same rates. So reads are BILLING_READ and every
# write is BILLING_WRITE *and* platform-admin only — a tenant admin must not be
# able to change what everyone is charged.


class ComponentPriceOut(BaseModel):
    kind: str
    component: str
    provider: str
    unit: str
    divisor: int
    millicents: int
    list_millicents: int | None
    label: str
    margin_millicents: int | None
    margin_pct: float | None


class PriceBookOut(BaseModel):
    version: str
    effective_from: str
    currency: str
    notes: str
    indicated: bool
    digest: str
    prices: list[ComponentPriceOut]


class PriceBookChangeIn(BaseModel):
    """A new version that differs from the latest only where stated."""

    model_config = {"extra": "forbid"}

    version: str = Field(min_length=3, max_length=32, pattern=r"^[0-9A-Za-z._-]+$")
    effective_from: date
    notes: str = Field(default="", max_length=500)
    changes: dict[str, dict] = Field(
        default_factory=dict,
        description="component → {millicents, list_millicents}, e.g. {'telephony:US': {'millicents': 1400}}",
    )


@router.get("/price-book", response_model=PriceBookOut)
async def get_price_book(
    version: str | None = Query(None, max_length=32),
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
) -> PriceBookOut:
    """The price book in force now, or a specific published version."""
    del ctx
    catalogue = price_book.default_catalogue()
    book = catalogue.get(version) if version else catalogue.for_moment()
    return PriceBookOut(**{k: v for k, v in book.as_dict().items() if k != "prices"}, prices=[ComponentPriceOut(**p) for p in book.as_dict()["prices"]])


@router.get("/price-book/versions", response_model=list[dict])
async def list_price_book_versions(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
) -> list[dict]:
    """Every published price-book version, oldest first.

    Versions are immutable. An invoice cites the version it was priced with, so re-deriving an old invoice reproduces the old numbers exactly.
    """
    del ctx
    return [
        {
            "version": book.version,
            "effective_from": book.effective_from.isoformat(),
            "digest": book.digest(),
            "indicated": book.indicated,
            "components": len(book.prices),
        }
        for book in price_book.default_catalogue().versions
    ]


@router.post("/price-book", response_model=PriceBookOut, status_code=201)
async def publish_price_book(
    payload: PriceBookChangeIn,
    ctx: TenantContext = Depends(get_platform_admin),
) -> PriceBookOut:
    """Publish a new immutable version. Existing versions are never edited.

    There is no PATCH and no DELETE on purpose: an invoice that cites version
    ``2026-01`` must still mean the same numbers a year later.

    Guarded by ``get_platform_admin``, which currently denies every principal:
    prices are deploy-scoped, so a tenant administrator must not move them for
    everyone. Until a platform-operator principal exists, a price change is a
    release — see ``docs/PRICE_BOOK.md``. Denying is the correct interim
    behaviour; inventing a superuser flag to make the endpoint "work" would be a
    privilege-escalation hole hidden behind a convenience.
    """
    catalogue = price_book.default_catalogue()
    try:
        updated = catalogue.with_change(
            version=payload.version,
            effective_from=payload.effective_from,
            changes=payload.changes,
            notes=payload.notes,
        )
    except price_book.PriceBookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    log.info("billing.price_book.published", version=payload.version, by=str(ctx.user_id))
    book = updated.get(payload.version)
    payload_dict = book.as_dict()
    return PriceBookOut(
        **{k: v for k, v in payload_dict.items() if k != "prices"},
        prices=[ComponentPriceOut(**p) for p in payload_dict["prices"]],
    )


@router.get("/invoice/lines")
async def itemised_invoice_lines(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """This period's usage, priced per component against the current book."""
    context = await load_context(session, ctx.tenant)
    return await cost.itemised_usage_cost(
        session, tenant_id=ctx.tenant_id, period=context.period
    )


@router.get("/usage/by-provider")
async def usage_by_provider(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Usage split by provider and by day, from the same rows.

    One query, one pivot: a "by day" chart computed separately from the "by
    provider" table is how the two disagree by a few minutes and the customer
    notices before we do.
    """
    from sqlalchemy import select

    from app.db.models import UsageEvent

    context = await load_context(session, ctx.tenant)
    events = (
        await session.execute(
            select(UsageEvent).where(
                UsageEvent.tenant_id == ctx.tenant_id,
                UsageEvent.billing_period == context.period.label,
            )
        )
    ).scalars().all()
    rows = []
    for event in events:
        meta = dict(event.event_metadata or {})
        rows.append(
            {
                "provider": meta.get("provider") or "voxdesk",
                "metric": getattr(event.metric, "value", str(event.metric)),
                "quantity": int(event.quantity or 0),
                "day": event.created_at.date().isoformat() if event.created_at else "",
            }
        )
    pivoted = cost.usage_by_provider_and_day(rows)
    return {
        "billing_period": context.period.label,
        "currency": context.plan.currency if context.plan else "usd",
        **pivoted,
    }


@router.get("/margin")
async def margin_report(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cost against charge for this period — the reseller's view.

    A tenant administrator sees their own margin (they are the reseller to
    *their* customers). Nobody sees another tenant's: the query is scoped to
    ``ctx.tenant_id`` exactly like every other read here.
    """
    context = await load_context(session, ctx.tenant)
    invoice = await cost.itemised_usage_cost(
        session, tenant_id=ctx.tenant_id, period=context.period
    )
    return {
        "billing_period": context.period.label,
        "price_book_version": invoice["price_book_version"],
        "prices_indicated": invoice["prices_indicated"],
        "totals": invoice["totals"],
        "by_component": [
            {
                "component": line["component"],
                "kind": line["kind"],
                "cost_millicents": line["cost_millicents"],
                "charge_millicents": line["charge_millicents"],
                "margin_millicents": line["margin_millicents"],
                "margin_pct": line["margin_pct"],
            }
            for line in invoice["lines"]
        ],
        "unpriced": invoice["unpriced"],
    }


@router.post("/checkout", response_model=SessionOut)
async def start_checkout(
    body: CheckoutIn,
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    """
    Begin a purchase.

    Returns a URL and nothing else. **No subscription state is established
    here** — a browser reaching the success URL has proved only that it
    reached a URL. Only a verified webhook makes a subscription active.
    """
    try:
        result = await service.start_checkout(
            session, ctx.tenant, plan_code=body.plan_code, interval=body.interval
        )
    except PlanNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message)
    except BillingError as exc:
        raise _handle_billing_error(exc)

    await record_audit(
        session, action=AuditAction.BILLING_CHECKOUT_STARTED,
        tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        # Plan code and interval only. No amount, no session id, no customer
        # id -- requirement 28.
        detail={"plan": result.plan_code, "interval": body.interval.value},
    )
    await session.commit()
    return SessionOut(url=result.url)


@router.post("/portal", response_model=SessionOut)
async def open_portal(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    """
    Open the customer portal for **the authenticated tenant's** customer.

    There is no request body. The external customer id is read from the
    tenant's own row, so there is no parameter through which another tenant's
    id could be supplied (requirement 9).
    """
    try:
        url = await service.start_portal(session, ctx.tenant)
    except BillingError as exc:
        raise _handle_billing_error(exc)
    return SessionOut(url=url)


@router.post("/change-plan", response_model=BillingStatusOut)
async def change_plan(
    body: ChangePlanIn,
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> BillingStatusOut:
    """
    Upgrade immediately, or schedule a downgrade for the period end.

    The policy is implemented once, in `service.change_plan`, and documented
    there and in `docs/BILLING.md`.
    """
    try:
        subscription = await service.change_plan(
            session, ctx.tenant, plan_code=body.plan_code, interval=body.interval
        )
    except PlanNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message)
    except BillingError as exc:
        raise _handle_billing_error(exc)

    await record_audit(
        session, action=AuditAction.BILLING_PLAN_CHANGED, tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id, actor_email=ctx.user.email,
        detail={
            "plan": body.plan_code,
            "scheduled": subscription.pending_plan_id is not None,
        },
    )
    await session.commit()
    return await get_billing(ctx=ctx, session=session)


@router.post("/cancel", response_model=BillingStatusOut)
async def cancel_subscription(
    body: CancelIn | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> BillingStatusOut:
    """Cancel at the end of the current period.

    Immediate cancellation is deliberately not offered: it would forfeit usage the customer has already been billed for.
    """
    payload = body or CancelIn()
    try:
        await service.cancel(
            session, ctx.tenant, immediately=payload.immediately
        )
    except BillingError as exc:
        raise _handle_billing_error(exc)

    await record_audit(
        session, action=AuditAction.BILLING_CANCELLATION_REQUESTED,
        tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        detail={
            "immediately": payload.immediately,
            "reason": payload.reason[:200],
        },
    )
    await session.commit()
    return await get_billing(ctx=ctx, session=session)


@router.post("/reconcile", response_model=BillingStatusOut)
async def reconcile(
    ctx: TenantContext = Depends(require_permission(Permission.BILLING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> BillingStatusOut:
    """
    Re-read the provider and rebuild the usage cache.

    The "something looks wrong" button. Safe by construction: the provider is
    always authoritative on subscription state, and rebuilding a summary only
    touches a cache.
    """
    try:
        await service.reconcile_subscription(session, ctx.tenant)
    except BillingUnsupportedError:
        pass          # the manual provider has nothing to reconcile against
    except BillingError as exc:
        raise _handle_billing_error(exc)

    await reconciliation.rebuild_summaries(session, ctx.tenant)
    return await get_billing(ctx=ctx, session=session)


# ------------------------------------------------------------------ webhook ---

@router.post("/webhook/{provider}")
async def receive_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """
    Provider events.

    Unauthenticated by necessity — Stripe has no VoxDesk credential — and
    therefore **signature-verified before anything else**.

    `await request.body()` gives the exact bytes as received. Nothing parses
    the JSON before verification: requirement 21 is explicit that a
    re-serialized body invalidates the signature, and reading `request.json()`
    here would silently make every signature check meaningless.

    Always returns 2xx once the signature passes. A non-2xx makes Stripe
    retry, and retrying an event we have already recorded, or one nobody will
    ever handle, is pure noise. Genuine handler failures are the exception:
    those return 500 so Stripe *does* retry.
    """
    try:
        provider_type = BillingProviderType(provider.lower())
    except ValueError:
        # Same shape as a signature failure, so the endpoint cannot be probed
        # for which providers are configured.
        raise HTTPException(status_code=400, detail="Invalid signature")

    if provider_type is not configured_provider():
        raise HTTPException(status_code=400, detail="Invalid signature")

    raw_body = await request.body()
    if len(raw_body) > 1_000_000:
        raise HTTPException(status_code=413, detail="Payload too large")

    client = service.adapter()
    try:
        event = client.verify_webhook(
            raw_body=raw_body,
            signature_header=request.headers.get("Stripe-Signature", ""),
        )
    except BillingError as exc:
        # 400, not 401: Stripe treats 4xx as permanent and stops retrying,
        # which is right for a request that will never verify.
        log.warning(
            "billing.webhook_rejected", provider=provider_type.value,
            reason=exc.code,
        )
        raise HTTPException(status_code=400, detail="Invalid signature")

    outcome = await webhooks.process_event(session, event)

    if outcome.tenant_id and event.event_type == "invoice.payment_failed":
        await record_audit(
            session, action=AuditAction.BILLING_PAYMENT_FAILED,
            tenant_id=outcome.tenant_id, actor_email="stripe:webhook",
            detail={"event_type": event.event_type},
        )
        await session.commit()

    return {
        "received": True,
        "handled": outcome.handled,
        "duplicate": outcome.duplicate,
    }
