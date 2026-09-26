"""Registry coverage for environment-scoped resources."""

from __future__ import annotations

from app.environments.resource_types import EXCLUDED_CONTROL_PLANE, RESOURCE_TYPES, all_types
from app.resources.registry import is_excluded, model_for, registered_names


def test_exactly_eight_resource_types_are_registered():
    names = registered_names()
    assert names == {
        "call", "lead", "appointment", "knowledge_document",
        "automation", "notification", "inbox", "usage_event",
    }
    assert len(all_types()) == 8
    assert len(RESOURCE_TYPES) == len(names)


def test_each_type_maps_to_one_model():
    seen = set()
    for spec in all_types():
        model = model_for(spec.type)
        assert model.__name__ == spec.model_name
        assert model.__tablename__ == spec.table_name
        assert spec.tenant_scoped is True
        assert spec.environment_scoped is True
        assert spec.legacy_default_environment_allowed is True
        assert model not in seen
        seen.add(model)


def test_control_plane_resources_stay_excluded():
    for name in (
        "subscription", "billing_plan", "sso_connection", "api_key", "service_account",
    ):
        assert name in EXCLUDED_CONTROL_PLANE
        assert is_excluded(name) is True
    assert "call" not in EXCLUDED_CONTROL_PLANE
