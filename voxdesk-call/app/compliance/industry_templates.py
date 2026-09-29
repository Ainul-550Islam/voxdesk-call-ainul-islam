"""Non-regulatory starter profiles for configured operational checks.

These profiles are convenience starting points, not legal or industry standards.
The tenant supplies the source references and approves the control values and
workflow fit before using a created framework.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StarterControl:
    control_key: str
    name: str
    description: str
    field: str

    def public_dict(self) -> dict[str, str]:
        return {
            "control_key": self.control_key,
            "name": self.name,
            "description": self.description,
            "observed_field": self.field,
            "expected_value": True,
            "operator": "equals",
            "severity": "medium",
            "source_reference_required": True,
            "review_on_mismatch": True,
        }


@dataclass(frozen=True)
class IndustryTemplate:
    key: str
    name: str
    framework_type: str
    version: str
    description: str
    limitations: tuple[str, ...]
    controls: tuple[StarterControl, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "framework_type": self.framework_type,
            "version": self.version,
            "description": self.description,
            "limitations": [
                limitation if "not a legal or regulatory standard" in limitation.lower()
                else f"{limitation} This template is not a legal or regulatory standard."
                for limitation in self.limitations
            ],
            "controls": [control.public_dict() for control in self.controls],
        }


_COMMON_LIMITATIONS = (
    "This is a generic operational starter, not a legal or regulatory standard.",
    "It does not establish compliance, certification, clinical suitability, or safety.",
    "Tenant administrators must review the workflow, observed fields, expected values, and source references before use.",
    "Execution evaluates only the tenant-configured values and persists findings through the existing compliance service.",
)

INDUSTRY_TEMPLATES: tuple[IndustryTemplate, ...] = (
    IndustryTemplate(
        key="healthcare-operations-basic",
        name="Healthcare operations — basic workflow checks",
        framework_type="healthcare",
        version="1.0.0",
        description="Optional checks for tenant-defined intake verification and appointment confirmation records.",
        limitations=_COMMON_LIMITATIONS + (
            "No diagnosis, triage recommendation, treatment suggestion, or clinical decision is performed.",
        ),
        controls=(
            StarterControl(
                "intake-verification-recorded",
                "Intake verification recorded",
                "Checks whether the tenant-supplied observation marks its intake verification step complete.",
                "intake_verified",
            ),
            StarterControl(
                "appointment-confirmation-recorded",
                "Appointment confirmation recorded",
                "Checks whether the tenant-supplied observation marks its appointment confirmation step complete.",
                "appointment_confirmed",
            ),
        ),
    ),
    IndustryTemplate(
        key="manufacturing-operations-basic",
        name="Manufacturing operations — basic workflow checks",
        framework_type="manufacturing",
        version="1.0.0",
        description="Optional checks for tenant-defined work-order identity and maintenance-window records.",
        limitations=_COMMON_LIMITATIONS + (
            "Does not actuate equipment, certify product quality, or determine worker or machine safety.",
        ),
        controls=(
            StarterControl(
                "work-order-identity-recorded",
                "Work-order identity recorded",
                "Checks whether the tenant-supplied observation marks work-order identity verification complete.",
                "work_order_identity_verified",
            ),
            StarterControl(
                "maintenance-window-recorded",
                "Maintenance window recorded",
                "Checks whether the tenant-supplied observation marks the maintenance-window step complete.",
                "maintenance_window_recorded",
            ),
        ),
    ),
    IndustryTemplate(
        key="retail-operations-basic",
        name="Retail operations — basic workflow checks",
        framework_type="retail",
        version="1.0.0",
        description="Optional checks for tenant-defined return-eligibility and refund-authorization records.",
        limitations=_COMMON_LIMITATIONS + (
            "Does not issue refunds, change orders, make payment decisions, or replace store policy.",
        ),
        controls=(
            StarterControl(
                "return-eligibility-recorded",
                "Return eligibility check recorded",
                "Checks whether the tenant-supplied observation marks the configured return-eligibility step complete.",
                "return_eligibility_checked",
            ),
            StarterControl(
                "refund-authorization-recorded",
                "Refund authorization recorded",
                "Checks whether the tenant-supplied observation marks the configured refund-authorization step complete.",
                "refund_authorization_recorded",
            ),
        ),
    ),
)

_TEMPLATE_BY_KEY = {template.key: template for template in INDUSTRY_TEMPLATES}


def list_industry_templates() -> list[dict[str, Any]]:
    return [template.public_dict() for template in INDUSTRY_TEMPLATES]


def resolve_industry_template(key: str) -> IndustryTemplate:
    try:
        return _TEMPLATE_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def framework_controls(template: IndustryTemplate, source_references: dict[str, str]) -> list[dict[str, Any]]:
    expected_keys = {control.control_key for control in template.controls}
    if set(source_references) != expected_keys:
        raise ValueError("source_references must provide exactly one source for every template control")
    controls: list[dict[str, Any]] = []
    for control in template.controls:
        source_reference = source_references[control.control_key].strip()
        if not source_reference or len(source_reference) > 1000:
            raise ValueError(f"source reference for {control.control_key} must contain 1 to 1000 characters")
        controls.append({
            "control_key": control.control_key,
            "name": control.name,
            "description": control.description,
            "severity": "medium",
            "source_reference": source_reference,
            "required_evidence": [source_reference],
            "rules": {
                "field": control.field,
                "operator": "equals",
                "expected": True,
                "review_on_mismatch": True,
            },
            "active": True,
        })
    return controls
