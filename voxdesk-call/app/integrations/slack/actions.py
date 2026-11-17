"""Slack action payloads and response interpretation."""

from __future__ import annotations

from typing import Any, Mapping

POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackActionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def post_message_payload(*, channel: str, text: str, thread_ts: str | None = None) -> dict[str, Any]:
    if not channel:
        raise SlackActionError("no_channel", "a channel is required")
    if not text:
        raise SlackActionError("no_text", "a message needs text")
    payload: dict[str, Any] = {"channel": channel, "text": text[:4000]}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return payload


def headers(bot_token: str) -> dict[str, str]:
    if not bot_token:
        raise SlackActionError("no_token", "no Slack bot token is configured")
    return {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json; charset=utf-8"}


def interpret(response: Mapping[str, Any]) -> dict[str, Any]:
    """Slack answers 200 with `ok: false` for most failures; never trust the code."""
    if not response.get("ok"):
        raise SlackActionError(
            "slack_error", str(response.get("error") or "unknown_error")[:200]
        )
    return {"channel": response.get("channel"), "ts": response.get("ts")}
