"""Canonical serialization and SHA-256 helpers used by governance records."""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import enum
import hashlib
import json
import uuid
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented deterministically."""


def _normalise(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return _normalise(value.value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {
            str(key): _normalise(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise CanonicalizationError(f"Unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the one JSON representation used for all governance hashes."""
    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonicalize_payload(value: Any) -> str:
    return canonical_json(value)


def hash_payload(value: Any) -> str:
    return sha256_hex(value)


def fingerprint(value: Any) -> str:
    return sha256_hex(value)


def calculate_chain_hash(
    previous_hash: str | None, payload_hash: str, metadata: Any
) -> str:
    return sha256_hex(
        {
            "previous_hash": previous_hash,
            "payload_hash": payload_hash,
            "metadata": metadata,
        }
    )


def chain_hash(
    *, previous_hash: str | None, payload: Any, event_type: str, sequence: int
) -> str:
    """Hash the immutable chain envelope, not a database repr."""
    return sha256_hex(
        {
            "event_type": event_type,
            "previous_hash": previous_hash,
            "sequence": sequence,
            "payload": payload,
        }
    )


def merkle_root(hashes: list[str]) -> str:
    """Build a deterministic binary Merkle root for a package of evidence."""
    if not hashes:
        return sha256_hex([])
    layer = list(hashes)
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [
            sha256_bytes((layer[index] + layer[index + 1]).encode("ascii"))
            for index in range(0, len(layer), 2)
        ]
    return layer[0]
