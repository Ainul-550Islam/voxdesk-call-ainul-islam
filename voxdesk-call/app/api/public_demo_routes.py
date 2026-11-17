"""Public request-a-call demo endpoints (PART 18, P18-03).

Unauthenticated, tenant-less, and therefore the smallest possible surface: four
endpoints, no tenant data in any response, every refusal carries a stable code
the page can explain.

``GET  /api/v1/public/demo``                  what the demo is, and is it open
``POST /api/v1/public/demo/verification``     send a code to a number you control
``POST /api/v1/public/demo/verification/confirm``  prove control, once
``POST /api/v1/public/demo/call``             place one demonstrated call

The service layer holds the gates; this module only translates them into HTTP
and keeps the dialler binding out of the tests' way.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.core.rate_limit import allow_identity_action, client_ip as trusted_client_ip
from app.db.session import get_session
from app.services import public_abuse_service, public_demo_service

router = APIRouter(prefix="/api/v1/public/demo", tags=["public-demo"])

#: The per-IP budget for the *endpoints*, independent of the call rate limit.
#: Without it an attacker can enumerate numbers through /verification for free.
VERIFICATION_REQUESTS_PER_HOUR = 5
VERIFICATION_CONFIRM_PER_HOUR = 10

DEMO_SALT_ENV = "DEMO_RATE_LIMIT_SALT"


def _salt() -> str:
    """A per-deployment salt for the hashed number/IP columns.

    Not a secret in the cryptographic sense — it exists so that a leaked dump
    cannot be rainbow-tabled back to phone numbers. Missing salt is a deployment
    fault and is refused rather than defaulted to a constant that every
    installation would share.
    """
    salt = os.environ.get(DEMO_SALT_ENV, "").strip()
    if not salt:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "demo_misconfigured",
                "message": f"{DEMO_SALT_ENV} is not set; the demo must not hash with a shared constant",
            },
        )
    return salt


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"number": "+15550100111"}]})

    number: str = Field(min_length=7, max_length=20, description="E.164, e.g. +15550100111")


class VerificationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"number": "+15550100111", "code": "482913"}]})

    number: str = Field(min_length=7, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class DemoCallBody(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"number": "+15550100111", "consent": True, "consent_version": "2026-11-08", "captcha_token": "03AGdBq..."}]})

    number: str = Field(min_length=7, max_length=20)
    consent: bool = Field(description="The visitor must tick this; the server re-checks it.")
    consent_version: str = Field(min_length=1, max_length=20)
    captcha_token: str | None = Field(default=None, max_length=4096)


def _http(exc: public_demo_service.DemoError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.as_dict())


async def _throttle(request: Request, action: str, limit: int) -> None:
    ip = trusted_client_ip(request)
    if not await allow_identity_action(
        action=f"public:demo:{action}", who=ip, limit=limit, window=3600.0
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "too many requests from this connection"},
        )


def default_code_sender() -> Callable[[str, str], Awaitable[None]] | None:
    """SMS the verification code. ``None`` when SMS is not configured.

    Returning None (rather than a stub that pretends to send) is what makes a
    misconfigured deployment visible: the route refuses instead of "verifying"
    a number nobody can receive a code on.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if str(getattr(settings, "sms_provider", "") or "") == "":
        return None

    async def _send(number: str, code: str) -> None:
        # The SMS adapter returns an outcome instead of raising, so a silent
        # failure here would mean a code that was never delivered and a visitor
        # who waits forever. Treat anything but "accepted" as a fault.
        from app.notifications.delivery import SmsAdapter

        body = (
            f"Your VoxDesk demo verification code is {code}. "
            "It expires in 15 minutes."
        )
        result = await SmsAdapter().send(recipient=number, body=body)
        if result.outcome != "accepted":
            raise public_demo_service.DemoError(
                "verification_send_failed",
                "the verification code could not be delivered to that number",
                502,
            )

    return _send


def default_dialler() -> Callable[[str, int], Awaitable[str]] | None:
    """Place the demo call through the configured telephony provider.

    Deliberately a thin binding: the demo call is an ordinary outbound call
    against the vendor's demo agent, created through the same service the
    dashboard uses, so the demo exercises the real path and not a mock of it.
    """
    from app.core.config import get_settings

    settings = get_settings()
    agent_id = str(getattr(settings, "demo_agent_id", "") or "")
    if not agent_id:
        return None

    from_number = str(getattr(settings, "demo_from_number", "") or "")
    answer_url = str(getattr(settings, "demo_answer_url", "") or "")

    async def _dial(number: str, max_seconds: int) -> str:
        # One dial through the same provider adapter the product uses. The
        # max_seconds cap is enforced after pickup by the call session, not by
        # the provider API — say so in the docs rather than pretend it is a
        # carrier-side limit.
        from app.telephony.provider import get_provider

        result = await get_provider().create_outbound(
            to_number=number, from_number=from_number, answer_url=answer_url
        )
        if not getattr(result, "ok", False) or not getattr(result, "external_id", ""):
            raise public_demo_service.DemoError(
                "dial_failed",
                "the telephony provider did not confirm the call",
                502,
                {"agent_id": agent_id, "max_seconds": max_seconds},
            )
        return str(result.external_id)

    return _dial


@router.get("")
async def demo_status(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Everything the public page needs, and nothing about any tenant."""
    config = public_demo_service.DemoConfig.from_settings()
    return await public_demo_service.demo_usage(session, config=config)


@router.post("/verification", status_code=status.HTTP_202_ACCEPTED)
async def request_verification(
    payload: VerificationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Send a one-time code to a number the visitor claims to control.

    Nothing is dialled until that code is confirmed, which is what stops the demo being pointed at somebody else's phone.
    """
    await _throttle(request, "verification", VERIFICATION_REQUESTS_PER_HOUR)
    sender = default_code_sender()
    if sender is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "demo_sms_unconfigured",
                "message": "the demo cannot send verification codes right now",
            },
        )
    try:
        result = await public_demo_service.request_verification(
            session, number=payload.number, send_code=sender
        )
    except public_demo_service.DemoError as exc:
        raise _http(exc) from None
    await session.commit()
    log.info("public.demo.verification_requested", destination=result["destination"])
    return result


@router.post("/verification/confirm")
async def confirm_verification(
    payload: VerificationConfirmRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Confirm the code and add the number to the demo allow-list.

    Single use, expires in 15 minutes, and five wrong attempts lock the number until a new code is requested.
    """
    await _throttle(request, "confirm", VERIFICATION_CONFIRM_PER_HOUR)
    try:
        result = await public_demo_service.confirm_verification(
            session, number=payload.number, code=payload.code
        )
    except public_demo_service.DemoError as exc:
        await session.commit()  # attempt counters must persist even on refusal
        raise _http(exc) from None
    await session.commit()
    log.info("public.demo.verification_confirmed", destination=result["destination"])
    return result


@router.post("/call", status_code=status.HTTP_201_CREATED)
async def create_demo_call(
    payload: DemoCallBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Place one demo call, or explain precisely why not."""
    config = public_demo_service.DemoConfig.from_settings()
    dialler = default_dialler()
    if dialler is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "demo_dialler_unconfigured",
                "message": "the demo line is not configured on this deployment",
            },
        )
    try:
        record = await public_demo_service.create_demo_call(
            session,
            number=payload.number,
            ip=trusted_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            consent=payload.consent,
            consent_version=payload.consent_version,
            captcha_token=payload.captcha_token,
            config=config,
            salt=_salt(),
            verifier=public_abuse_service.default_verifier(),
            dialler=dialler,
        )
    except public_demo_service.DemoError as exc:
        await session.commit()  # rate-limit and refusal counters must persist
        log.info("public.demo.call_refused", code=exc.code)
        raise _http(exc) from None
    await session.commit()
    log.info("public.demo.call_placed", request_id=record["id"], destination=record["destination"])
    return record
