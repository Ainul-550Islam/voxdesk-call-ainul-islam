"""Public chat endpoint with abuse gates (PART 14, P14-01/02).

`POST /api/v1/public/chat/session` is the only unauthenticated endpoint that can
spend money, so it does four things in this order, cheapest first:

1. authenticate the public key against the request Origin
   (`authenticate_public_key`) -- this is the existing, reviewed path;
2. check the toll-fraud heuristics on the visitor's recent activity;
3. verify the captcha token against the key's configured provider/threshold;
4. consume one request from the key's daily budget.

Only then does it create a widget session. Anything refused returns the specific
reason (`captcha_missing`, `budget_seconds_exhausted`, ...), because a support
engineer needs to know which gate fired, and the codes are the same ones the
service layer raises.

The embedded widget (`widget/chat.js`) posts here; its tests run against a stub
`fetch` and assert on these codes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import client_ip as client_ip_from_request
from app.db.session import get_session
from app.domain.public_widget_models import (
    PublicWidgetSessionCreateRequest,
    PublicWidgetSessionMode,
)
from app.services import public_abuse_service, public_key_service, public_widget_service

router = APIRouter(prefix="/api/v1/public/chat", tags=["public-chat"])

_ABUSE_STATUS = {
    "captcha_missing": 400,
    "captcha_failed": 403,
    "captcha_low_score": 403,
    "captcha_error": 503,
    "captcha_unconfigured": 503,
    "budget_requests_exhausted": 429,
    "budget_seconds_exhausted": 429,
    "toll_fraud_rapid": 429,
    "toll_fraud_destinations": 429,
}


class ChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"public_key": "vd_pk_live_9f2c...", "origin": "https://acme.example.com", "visitor_id": "v-8f21", "captcha_token": "03AGdBq..."}]})

    public_key: str = Field(min_length=12, max_length=256)
    origin: str | None = Field(default=None, max_length=255)
    visitor_id: str | None = Field(default=None, max_length=120)
    captcha_token: str | None = Field(default=None, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _abuse_error(decision: public_abuse_service.Decision) -> HTTPException:
    return HTTPException(
        status_code=_ABUSE_STATUS.get(decision.code, status.HTTP_429_TOO_MANY_REQUESTS),
        detail={"code": decision.code, "message": decision.reason, "detail": decision.detail},
    )


@router.post("/session", status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    payload: ChatSessionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Open a chat session for the embedded chat widget.

    Gates run cheapest-first — key and origin, then toll-fraud heuristics, then captcha, then the daily budget — so a bot cannot make the server do expensive work before being refused.
    """
    ip = client_ip_from_request(request)
    origin = payload.origin or request.headers.get("origin")

    key_row, normalized_origin = await public_key_service.authenticate_public_key(
        session,
        raw_public_key=payload.public_key,
        request_origin=origin,
        required_capability=public_key_service.PublicWidgetCapability.WIDGET_SESSION_CREATE,
        client_ip=ip,
    )

    decision = await public_abuse_service.evaluate(
        session,
        tenant_id=key_row.tenant_id,
        public_key_id=key_row.id,
        widget_config=key_row.widget_config,
        captcha_token=payload.captcha_token,
        verifier=public_abuse_service.default_verifier(),
        recent_calls=0,
        seconds=0,
    )
    if not decision.allowed:
        await session.commit()  # keep the refusal counter
        raise _abuse_error(decision)

    widget_session = await public_widget_service.create_public_widget_session(
        session,
        payload=PublicWidgetSessionCreateRequest(
            public_key=payload.public_key,
            mode=PublicWidgetSessionMode.CHAT,
            visitor_id=payload.visitor_id,
            origin=normalized_origin,
            metadata=payload.metadata,
        ),
        request_origin=normalized_origin,
        client_ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()

    usage = await public_abuse_service.usage(
        session, tenant_id=key_row.tenant_id, public_key_id=key_row.id
    )
    return {
        "session": widget_session.model_dump(mode="json"),
        "budget": usage,
        "captcha": {"provider": (key_row.widget_config or {}).get("captcha", {}).get("provider")},
    }


@router.get("/budget")
async def budget(
    public_key: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Today's counters for this key. Read-only; consumes nothing."""
    key_row, _ = await public_key_service.authenticate_public_key(
        session,
        raw_public_key=public_key,
        request_origin=None,
        required_capability=public_key_service.PublicWidgetCapability.WIDGET_SESSION_CREATE,
    )
    config = public_abuse_service.BudgetConfig.from_widget_config(key_row.widget_config)
    usage = await public_abuse_service.usage(
        session, tenant_id=key_row.tenant_id, public_key_id=key_row.id
    )
    return {
        "usage": usage,
        "limits": config.as_dict(),
        "remaining_requests": max(config.daily_requests - usage["requests"], 0),
        "remaining_seconds": max(config.daily_seconds - usage["seconds_used"], 0),
    }
