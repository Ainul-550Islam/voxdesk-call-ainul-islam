"""Slack OAuth v2 install flow."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping
from urllib.parse import urlencode

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"
#: A workspace install must be able to identify itself before it can post.
BASE_SCOPES = ("chat:write",)


class OAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


HttpPost = Callable[[str, Mapping[str, str], Mapping[str, str]], Awaitable[Mapping[str, Any]]]


def new_state() -> str:
    """Single-use CSRF state. Stored server-side by the caller, never in a cookie."""
    return secrets.token_urlsafe(24)


def authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: Iterable[str] = (),
) -> str:
    if not client_id:
        raise OAuthError("no_client_id", "a Slack client id is required")
    if not redirect_uri.startswith("https://"):
        raise OAuthError("bad_redirect", "the Slack redirect URI must be https")
    scope_list = list(dict.fromkeys([*BASE_SCOPES, *scopes]))
    query = urlencode(
        {
            "client_id": client_id,
            "scope": ",".join(scope_list),
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


@dataclass(frozen=True)
class Install:
    team_id: str
    team_name: str
    bot_token: str
    scopes: tuple[str, ...]
    bot_user_id: str | None = None


async def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    post: HttpPost,
) -> Install:
    """Exchange an authorization code. The transport is injected."""
    if not code:
        raise OAuthError("no_code", "Slack returned no code")
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    response = await post(TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, payload)
    if not response.get("ok"):
        raise OAuthError("exchange_failed", str(response.get("error") or "unknown_error"))
    team = response.get("team") or {}
    return Install(
        team_id=str(team.get("id") or ""),
        team_name=str(team.get("name") or ""),
        bot_token=str(response.get("access_token") or ""),
        scopes=tuple(str(response.get("scope") or "").split(",")),
        bot_user_id=response.get("bot_user_id"),
    )
