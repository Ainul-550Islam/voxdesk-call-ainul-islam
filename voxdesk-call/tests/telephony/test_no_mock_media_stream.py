"""Verification that mock media-stream and fallback token paths are removed (PART 3).

Closes:
- `F-05`: `app/telephony/realtime.py::RealtimeVoiceSessionOrchestrator.handle_ws_message`
  and `/calls/{call_id}/media-stream` in `app/api/v1/telephony_routes.py`.
- `F-06`: `tok_{uuid}_{call_id}` fallback token in `app/api/outbound_call_routes.py`.
"""

from __future__ import annotations

from pathlib import Path

from app.main import app
import app.telephony.media_gateway as media_gateway
import app.telephony.realtime as realtime
import app.telephony.web_transport as web_transport

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_realtime_module_has_no_fabricated_orchestrator_or_ws_handler() -> None:
    assert not hasattr(realtime, "RealtimeVoiceSessionOrchestrator")
    assert not hasattr(realtime, "handle_ws_message")

    source = (REPO_ROOT / "app" / "telephony" / "realtime.py").read_text(encoding="utf-8")
    assert "def handle_ws_message" not in source
    assert "class RealtimeVoiceSessionOrchestrator" not in source
    assert "I heard:" not in source
    assert "Hello, I am ready" not in source
    assert 'reply_text.encode("utf-8")' not in source
    assert 'greeting.encode("utf-8")' not in source


def test_telephony_routes_deleted_mock_media_stream_endpoint() -> None:
    source = (REPO_ROOT / "app" / "api" / "v1" / "telephony_routes.py").read_text(
        encoding="utf-8"
    )
    assert "/calls/{call_id}/media-stream" not in source
    assert "@router.websocket" not in source
    assert "handle_ws_message" not in source

    route_paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/v1/telephony/calls/{call_id}/media-stream" not in route_paths
    assert "/telephony/web/ws" in route_paths
    assert "/telephony/web/offer" in route_paths


def test_outbound_call_routes_has_no_fallback_tok_uuid_tokens() -> None:
    source = (REPO_ROOT / "app" / "api" / "outbound_call_routes.py").read_text(
        encoding="utf-8"
    )
    assert "tok_" not in source
    assert "_WEB_CALL_SESSIONS" not in source
    assert "web_call_service.create_web_call" in source


def test_media_gateway_is_wired_to_real_web_transport() -> None:
    assert hasattr(media_gateway, "media_gateway_manager")
    assert web_transport.media_gateway_manager is media_gateway.media_gateway_manager
