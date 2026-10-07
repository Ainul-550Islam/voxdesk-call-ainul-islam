"""Narrow optional decoded JSON containers without copying mutable snapshots."""
from __future__ import annotations

from typing import Any


def dictionary_value(value: object) -> dict[str, Any]:
    """Return a dictionary unchanged, or an empty dictionary for another shape."""
    return value if isinstance(value, dict) else {}


def list_value(value: object) -> list[Any]:
    """Return a list unchanged, or an empty list for another shape."""
    return value if isinstance(value, list) else []
