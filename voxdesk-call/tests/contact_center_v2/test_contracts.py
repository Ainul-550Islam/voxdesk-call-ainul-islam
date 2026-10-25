import inspect

from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from app.main import app
from app.telephony.twilio_handler import incoming_call


def test_every_cc2_route_is_mounted_with_permission_and_audit_dependencies():
    routes = [route for route in app.routes if getattr(route, "path", "").startswith("/api/cc2/")]
    assert routes
    prefixes = {
        "/api/cc2/dialer/",
        "/api/cc2/softphone/",
        "/api/cc2/assist/",
        "/api/cc2/acd/",
        "/api/cc2/supervisor/",
    }
    assert {prefix for prefix in prefixes if any(route.path.startswith(prefix) for route in routes)} == prefixes
    http_routes = [route for route in routes if isinstance(route, APIRoute)]
    assert http_routes
    for route in http_routes:
        source = inspect.getsource(route.endpoint)
        assert "require_permission(Permission." in source, route.path
        assert "await audit(" in source or "await _control(" in source, route.path
    websocket_routes = [
        route for route in app.routes
        if isinstance(route, WebSocketRoute)
        and getattr(route, "path", "").startswith(("/api/cc2/", "/ws/cc2/"))
    ]
    assert len(websocket_routes) == 3
    for route in websocket_routes:
        source = inspect.getsource(route.endpoint)
        assert (
            "consume_websocket_ticket" in source
            or "verify_cc2_ws_ticket" in source
            or "verify_realtime_ticket" in source
        ), route.path
        assert "record_audit" in source or "await audit(" in source, route.path


def test_cc2_voice_branch_is_after_twilio_signature_verification():
    source = inspect.getsource(incoming_call)
    assert source.index("await _verify_twilio(request)") < source.index("await handle_voice(")
