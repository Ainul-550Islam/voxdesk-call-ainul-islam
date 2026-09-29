"""Enterprise AI governance foundation.

Importing this package registers its SQLAlchemy models with the application's
shared ``Base.metadata``. It does not import or replace ``app.ai.runtime``.
Submodules are exposed lazily so importing the DB model module never creates a
service-layer import cycle.
"""

from __future__ import annotations

import importlib

from . import models as _models  # noqa: F401

_SUBMODULES = {
    "access",
    "attestation",
    "audit_bridge",
    "dependencies",
    "evidence",
    "events",
    "hashing",
    "lineage",
    "model_registry",
    "policy",
    "repository",
    "residency",
    "retention",
    "risk",
    "service",
}

__all__ = ["_models", *_SUBMODULES]


def __getattr__(name: str):
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(name)
