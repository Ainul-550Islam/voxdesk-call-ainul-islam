"""Google OAuth 2.0 with PKCE for the Drive scope."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping
from urllib.parse import urlencode

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
#: `drive.readonly` only: a knowledge source must not be able to change a
#: customer's Drive, and asking for write scope would fail most security reviews.
DEFAULT_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)


class OAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


HttpPost = Callable[[str, Mapping[str, str], Mapping[str, str]], Awaitable[Mapping[str, Any]]]


def pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` per RFC 7636 S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorize_url(
    *, client_id: str, redirect_uri: str, state: str, challenge: str, scopes: Iterable[str] = ()
) -> str:
    if not client_id:
        raise OAuthError("no_client_id", "a Google client id is required")
    scope_list = list(dict.fromkeys([*DEFAULT_SCOPES, *scopes]))
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scope_list),
            "access_type": "offline",
            # Without `consent` Google returns a refresh token only the first
            # time, and a re-install silently gets a one-hour token.
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str


async def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
    post: HttpPost,
) -> Tokens:
    if not code:
        raise OAuthError("no_code", "Google returned no code")
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    response = await post(TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, payload)
    if "error" in response:
        raise OAuthError("exchange_failed", str(response.get("error_description") or response["error"]))
    return Tokens(
        access_token=str(response.get("access_token") or ""),
        refresh_token=response.get("refresh_token"),
        expires_in=int(response.get("expires_in") or 0),
        scope=str(response.get("scope") or ""),
    )


async def refresh(
    *, client_id: str, client_secret: str, refresh_token: str, post: HttpPost
) -> Tokens:
    if not refresh_token:
        # A missing refresh token means the install must be repeated; refreshing
        # will never succeed and hiding that produces a mysterious 401 later.
        raise OAuthError("no_refresh_token", "this install has no refresh token; re-install")
    response = await post(
        TOKEN_URL,
        {"Content-Type": "application/x-www-form-urlencoded"},
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if "error" in response:
        raise OAuthError("refresh_failed", str(response.get("error_description") or response["error"]))
    return Tokens(
        access_token=str(response.get("access_token") or ""),
        refresh_token=refresh_token,
        expires_in=int(response.get("expires_in") or 0),
        scope=str(response.get("scope") or ""),
    )
