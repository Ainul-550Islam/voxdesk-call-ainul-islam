"""ASGI request-body boundary checks for all HTTP application routes.

The reverse proxy has its own request ceiling, but the application also needs
an independent guard for direct/internal traffic and chunked requests without a
Content-Length header. The middleware counts actual ASGI body bytes; it does not
trust a client-supplied size header.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]

_TOO_LARGE = json.dumps(
    {
        "error": {
            "code": "request_too_large",
            "message": "Request body exceeds the configured limit",
        }
    },
    separators=(",", ":"),
).encode("utf-8")


class RequestBodyLimitMiddleware:
    """Enforce a byte ceiling for known-length and streamed HTTP bodies."""

    def __init__(self, app, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max(1, int(max_bytes))

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length: int | None = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value.decode("ascii"))
                except (UnicodeDecodeError, ValueError):
                    await self._send_error(send, 400, "invalid_content_length", "Invalid Content-Length")
                    return
                if content_length < 0:
                    await self._send_error(send, 400, "invalid_content_length", "Invalid Content-Length")
                    return
                break
        if content_length is not None and content_length > self.max_bytes:
            await self._send_error(send, 413, "request_too_large", "Request body exceeds the configured limit")
            return

        state: dict[str, Any] = {"bytes": 0, "oversized": False, "response_started": False, "body_sent": False}

        async def limited_receive() -> ASGIMessage:
            if state["oversized"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                state["bytes"] += len(message.get("body", b""))
                if state["bytes"] > self.max_bytes:
                    state["oversized"] = True
                    request_state = scope.setdefault("state", {})
                    request_state["request_body_too_large"] = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def limited_send(message: ASGIMessage) -> None:
            if not state["oversized"]:
                await send(message)
                if message.get("type") == "http.response.start":
                    state["response_started"] = True
                return

            message_type = message.get("type")
            if message_type == "http.response.start" and not state["response_started"]:
                state["response_started"] = True
                body = _TOO_LARGE
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"cache-control", b"no-store"),
                        ],
                    }
                )
            elif message_type == "http.response.body" and not state["body_sent"]:
                state["body_sent"] = True
                await send({"type": "http.response.body", "body": _TOO_LARGE, "more_body": False})

        await self.app(scope, limited_receive, limited_send)
        if state["oversized"] and not state["response_started"]:
            await self._send_error(send, 413, "request_too_large", "Request body exceeds the configured limit")

    @staticmethod
    async def _send_error(send: Send, status_code: int, code: str, message: str) -> None:
        body = _TOO_LARGE if status_code == 413 else json.dumps(
            {"error": {"code": code, "message": message}}, separators=(",", ":")
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


def add_security_middleware(app, *, max_request_body_bytes: int) -> None:
    """Install the body-size gate; headers, correlation and auth stay in their owners."""
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=max_request_body_bytes)
