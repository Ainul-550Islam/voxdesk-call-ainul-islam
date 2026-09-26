"""Backward-compatible import site for session routes.

The implementation lives in ``app.api.security_session_routes``. This module
keeps ``from app.api.session_routes import router`` working, so existing path
registration and clients are unchanged.
"""

from app.api.security_session_routes import router

__all__ = ["router"]
