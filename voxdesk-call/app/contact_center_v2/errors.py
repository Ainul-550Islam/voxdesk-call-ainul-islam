"""Stable errors returned by every Batch 04 domain service."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ContactCenterV2Error(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class NotFound(ContactCenterV2Error):
    def __init__(self, resource: str = "resource") -> None:
        super().__init__("not_found", f"{resource} was not found", status_code=404)


class NotConfigured(ContactCenterV2Error):
    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        super().__init__(
            "not_configured",
            message,
            status_code=501,
            detail={"missing": missing or []},
        )


class ProviderFailure(ContactCenterV2Error):
    def __init__(self, message: str = "The telephony provider rejected the operation") -> None:
        super().__init__("provider_failure", message, status_code=502)


class ProviderAmbiguous(ContactCenterV2Error):
    def __init__(self, message: str = "The provider outcome is ambiguous; reconcile before retrying") -> None:
        super().__init__("provider_outcome_ambiguous", message, status_code=409)


class Conflict(ContactCenterV2Error):
    def __init__(self, message: str, *, code: str = "conflict") -> None:
        super().__init__(code, message, status_code=409)


class Validation(ContactCenterV2Error):
    def __init__(self, message: str, *, code: str = "validation_error") -> None:
        super().__init__(code, message, status_code=422)


class PermissionDenied(ContactCenterV2Error):
    def __init__(self, message: str, *, code: str = "forbidden") -> None:
        super().__init__(code, message, status_code=403)


def to_http(exc: ContactCenterV2Error) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.as_dict())
