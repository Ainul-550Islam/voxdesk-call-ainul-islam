"""Tests for all 9 field transforms, validation errors, and dry-run mapping preview."""

from __future__ import annotations

from app.integrations_v2.core.field_mapping import (
    MappingRuleSpec,
    apply_transform,
    execute_field_mapping,
)


def test_all_nine_transforms_execute_deterministically():
    rec = {
        "first": "Ada",
        "last": "Lovelace",
        "nested": {"customer": {"tier": "enterprise"}},
    }
    assert apply_transform("identity", "hello", source_record=rec) == "hello"
    assert apply_transform("e164_phone", "(415) 555-0142", source_record=rec) == "+14155550142"
    assert (
        apply_transform("lowercase_email", "  Ada@Analytical.Engine ", source_record=rec)
        == "ada@analytical.engine"
    )
    iso = apply_transform("iso8601_ts", "2026-10-24T12:30:00Z", source_record=rec)
    assert iso == "2026-10-24T12:30:00+00:00"

    mapped_enum = apply_transform(
        "enum_map",
        "P1",
        source_record=rec,
        config={"mapping": {"P1": "urgent", "P2": "high"}, "fallback": "normal"},
    )
    assert mapped_enum == "urgent"

    concat_val = apply_transform(
        "concat",
        None,
        source_record=rec,
        config={"fields": ["first", "last"], "separator": " "},
    )
    assert concat_val == "Ada Lovelace"

    assert apply_transform(
        "split_name", "Grace Hopper", source_record=rec, config={"part": "first"}
    ) == "Grace"
    assert apply_transform(
        "split_name", "Grace Hopper", source_record=rec, config={"part": "last"}
    ) == "Hopper"

    assert (
        apply_transform(
            "default_if_null", "", source_record=rec, config={"default": "inbound_voice"}
        )
        == "inbound_voice"
    )
    assert (
        apply_transform(
            "json_extract", None, source_record=rec, config={"path": "nested.customer.tier"}
        )
        == "enterprise"
    )


def test_execute_field_mapping_preview_inbound_and_missing_required():
    rules = [
        MappingRuleSpec(
            entity_type="Contact",
            voxdesk_field="phone",
            external_field="mobile_phone",
            transform="e164_phone",
            required=True,
        ),
        MappingRuleSpec(
            entity_type="Contact",
            voxdesk_field="email",
            external_field="email_addr",
            transform="lowercase_email",
            required=True,
        ),
        MappingRuleSpec(
            entity_type="Contact",
            voxdesk_field="custom_fields.priority",
            external_field="urgency_code",
            transform="enum_map",
            transform_config={"mapping": {"1": "urgent"}, "fallback": "normal"},
        ),
    ]

    good = execute_field_mapping(
        rules,
        {"mobile_phone": "415-555-0100", "email_addr": "USER@Example.com", "urgency_code": "1"},
        direction="inbound",
    )
    assert good.ok is True
    assert good.output == {
        "phone": "+14155550100",
        "email": "user@example.com",
        "custom_fields": {"priority": "urgent"},
    }

    bad = execute_field_mapping(
        rules,
        {"mobile_phone": "", "email_addr": "not-an-email"},
        direction="inbound",
    )
    assert bad.ok is False
    assert len(bad.errors) == 2
