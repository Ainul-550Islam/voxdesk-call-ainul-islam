"""Telephony provider package.

Exports are lazy so importing the package does not import the Twilio SDK or
FastAPI. The live voice routes keep using ``twilio_handler`` directly.
"""

from __future__ import annotations

__all__ = [
    "ProviderBinding",
    "TelephonyAdapter",
    "build",
    "public_config",
    "select",
]


def __getattr__(name: str):
    if name in {"ProviderBinding", "build", "public_config", "select"}:
        from app.telephony.providers import factory

        return getattr(factory, name)
    if name == "TelephonyAdapter":
        from app.telephony.providers.base import TelephonyAdapter

        return TelephonyAdapter
    raise AttributeError(name)
