from __future__ import annotations

import pytest

from app.compliance.industry_templates import (
    framework_controls,
    list_industry_templates,
    resolve_industry_template,
)


@pytest.mark.parametrize(
    ("template_key", "framework_type", "field_names"),
    [
        (
            "healthcare-operations-basic",
            "healthcare",
            {"intake_verified", "appointment_confirmed"},
        ),
        (
            "manufacturing-operations-basic",
            "manufacturing",
            {"work_order_identity_verified", "maintenance_window_recorded"},
        ),
        (
            "retail-operations-basic",
            "retail",
            {"return_eligibility_checked", "refund_authorization_recorded"},
        ),
    ],
)
def test_starter_profile_creates_only_explicit_source_bound_boolean_checks(
    template_key: str,
    framework_type: str,
    field_names: set[str],
) -> None:
    template = resolve_industry_template(template_key)
    assert template.framework_type == framework_type
    references = {
        control.control_key: f"tenant://workflow/{control.control_key}"
        for control in template.controls
    }

    controls = framework_controls(template, references)

    assert len(controls) == len(template.controls)
    assert {control["rules"]["field"] for control in controls} == field_names
    for control in controls:
        assert control["rules"] == {
            "field": control["rules"]["field"],
            "operator": "equals",
            "expected": True,
            "review_on_mismatch": True,
        }
        assert control["source_reference"] in control["required_evidence"]
        assert control["severity"] == "medium"
        assert control["active"] is True


def test_template_rejects_missing_extra_and_blank_source_references() -> None:
    template = resolve_industry_template("retail-operations-basic")
    keys = {control.control_key for control in template.controls}
    complete = {key: f"tenant://policy/{key}" for key in keys}

    with pytest.raises(ValueError, match="exactly one source"):
        framework_controls(template, {})
    with pytest.raises(ValueError, match="exactly one source"):
        framework_controls(template, {**complete, "unexpected": "tenant://policy/other"})
    with pytest.raises(ValueError, match="1 to 1000 characters"):
        framework_controls(template, {**complete, next(iter(keys)): "   "})


def test_templates_disclose_their_non_regulatory_and_non_clinical_scope() -> None:
    templates = list_industry_templates()
    assert {template["framework_type"] for template in templates} == {
        "healthcare",
        "manufacturing",
        "retail",
    }
    healthcare = next(
        template for template in templates if template["framework_type"] == "healthcare"
    )
    assert any("No diagnosis" in limitation for limitation in healthcare["limitations"])
    assert all(
        "not a legal or regulatory standard" in limitation
        for template in templates
        for limitation in template["limitations"]
    )
