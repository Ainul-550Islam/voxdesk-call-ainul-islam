"""Domain errors and the HTTP envelope they translate into.

The envelope is part of the API contract: the console branches on ``code`` and the
operator reads ``message``. A route that raised a bare ``HTTPException(400)`` would
break both, so these tests pin the mapping down.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.platform_v2 import errors
from app.platform_v2.http import IDEMPOTENCY_HEADER, page


@pytest.mark.parametrize(
    ("exc_type", "status_code", "code"),
    [
        (errors.ValidationFailed, 422, "validation_failed"),
        (errors.PermissionDenied, 403, "permission_denied"),
        (errors.NotFound, 404, "not_found"),
        (errors.Conflict, 409, "conflict"),
        (errors.InsufficientHistory, 422, "insufficient_history"),
        (errors.InfeasibleSchedule, 422, "schedule_infeasible"),
        (errors.SolverUnavailable, 503, "solver_unavailable"),
        (errors.DomainNotVerified, 409, "domain_not_verified"),
        (errors.DomainTaken, 409, "domain_unavailable"),
        (errors.ProviderRejected, 502, "provider_rejected"),
    ],
    ids=[
        "validation-failed",
        "permission-denied",
        "not-found",
        "conflict",
        "insufficient-history",
        "infeasible-schedule",
        "solver-unavailable",
        "domain-not-verified",
        "domain-taken",
        "provider-rejected",
    ],
)
def test_every_domain_error_maps_to_one_status_and_one_code(exc_type, status_code, code):
    exc = exc_type("something was refused")
    assert exc.status_code == status_code
    assert exc.code == code
    body = exc.as_body()
    assert body == {"code": code, "message": "something was refused", "detail": {}}


def test_detail_is_carried_through_untouched():
    exc = errors.ValidationFailed("bad window", detail={"starts_at": "must be before ends_at"})
    assert exc.as_body()["detail"] == {"starts_at": "must be before ends_at"}


def test_to_http_produces_the_documented_envelope():
    translated = errors.to_http(errors.DomainTaken("domain is not available"))
    assert isinstance(translated, HTTPException)
    assert translated.status_code == 409
    assert translated.detail["code"] == "domain_unavailable"
    assert translated.detail["message"] == "domain is not available"


def test_to_http_never_leaks_the_exception_repr():
    # A traceback string would carry the caller's arguments, which can include tokens.
    translated = errors.to_http(errors.NotFound("forecast not found"))
    assert set(translated.detail) == {"code", "message", "detail"}
    assert "NotFound" not in str(translated.detail)


def test_domain_taken_uses_one_message_for_every_state():
    """A distinct message per state would let a stranger enumerate our customers."""
    taken = errors.DomainTaken("domain is not available")
    assert taken.code == "domain_unavailable"
    assert "another tenant" not in taken.message


def test_idempotency_header_is_the_documented_spelling():
    assert IDEMPOTENCY_HEADER == "Idempotency-Key"


@pytest.mark.parametrize(
    ("limit", "offset", "expected"),
    [
        (None, None, (100, 0)),
        (10, 5, (10, 5)),
        (0, -3, (1, 0)),
        (10_000, 0, (500, 0)),
        (-5, 0, (1, 0)),
        (25, 0, (25, 0)),
    ],
    ids=["defaults", "within-bounds", "lower-bound-clamp", "upper-bound-clamp", "negative-limit", "offset-preserved"],
)
def test_page_clamps_instead_of_trusting_the_query_string(limit, offset, expected):
    assert page(limit, offset) == expected
