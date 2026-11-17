"""A Slack client that takes its transport, so tests never touch the network."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from app.integrations.slack import actions

Http = Callable[[str, Mapping[str, str], Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class SlackClient:
    def __init__(self, *, bot_token: str, http: Http) -> None:
        self._token = bot_token
        self._http = http

    async def post_message(
        self, *, channel: str, text: str, thread_ts: str | None = None
    ) -> dict[str, Any]:
        payload = actions.post_message_payload(channel=channel, text=text, thread_ts=thread_ts)
        response = await self._http(actions.POST_MESSAGE_URL, actions.headers(self._token), payload)
        return actions.interpret(response)
