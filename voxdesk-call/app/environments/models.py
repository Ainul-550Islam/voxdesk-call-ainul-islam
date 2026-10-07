"""Environment vocabulary. The ORM mapping is ``app.db.models.Environment``."""

from __future__ import annotations

import enum
import re

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class EnvironmentKind(str, enum.Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class EnvironmentStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class InputError(ValueError):
    pass


DEFAULT_KIND = EnvironmentKind.PRODUCTION
DEFAULT_SLUG = "production"
DEFAULT_NAME = "Production"


def parse_kind(value: str) -> str:
    try:
        return EnvironmentKind(value).value
    except ValueError as exc:
        raise InputError("Environment kind must be development, staging or production") from exc


def parse_status(value: str) -> str:
    try:
        return EnvironmentStatus(value).value
    except ValueError as exc:
        raise InputError("Unknown environment status") from exc


def validate_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > 120:
        raise InputError("Environment name must be 1-120 characters")
    return cleaned


def normalize_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not _SLUG.match(slug) or not (3 <= len(slug) <= 63):
        raise InputError(
            "Environment slug must be 3-63 characters: lowercase letters, digits and hyphens"
        )
    return slug


def slug_from_name(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:48]
    if len(base) < 3:
        base = "env"
    return base[:63]
