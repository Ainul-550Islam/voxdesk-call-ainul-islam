"""Security policy primitives shared by integrations, tools and HTTP routes.

Exports are lazy so importing the package does not eagerly load configuration
or application/database modules.
"""
from __future__ import annotations

__all__ = ["csrf_origin_rejected"]


def __getattr__(name: str):
    if name == "csrf_origin_rejected":
        from app.security.csrf import csrf_origin_rejected
        return csrf_origin_rejected
    raise AttributeError(name)
