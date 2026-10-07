"""Canonical enterprise audit event vocabulary.

The existing ``AuditAction`` enum remains the compatibility/storage action
column. New and more specific event names are also stored in the additive
``AuditLog.event_type`` string field; unknown names map to the established
``governance_event`` action without losing their canonical event type.
"""
from __future__ import annotations

import re
from enum import StrEnum

from app.db.models import AuditAction

_EVENT_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class AuditEventType(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    FAILED_LOGIN = "failed_login"
    PASSWORD_CHANGE = "password_change"
    SESSION_REVOKED = "session_revoked"
    API_KEY_CREATED = "api_key_created"
    API_KEY_ROTATED = "api_key_rotated"
    API_KEY_REVOKED = "api_key_revoked"
    MEMBER_INVITED = "member_invited"
    MEMBER_ROLE_CHANGED = "member_role_changed"
    MEMBER_REMOVED = "member_removed"
    AGENT_CREATED = "agent_created"
    AGENT_UPDATED = "agent_updated"
    AGENT_DELETED = "agent_deleted"
    PHONE_NUMBER_ADDED = "phone_number_added"
    PHONE_NUMBER_UPDATED = "phone_number_updated"
    CALL_STARTED = "call_started"
    CALL_TRANSFERRED = "call_transferred"
    CALL_ENDED = "call_ended"
    WORKFLOW_EXECUTED = "workflow_executed"
    CAMPAIGN_STARTED = "campaign_started"
    BILLING_CHANGED = "billing_changed"
    SECURITY_SETTING_CHANGED = "security_setting_changed"
    WEBHOOK_CONFIGURATION_CHANGED = "webhook_configuration_changed"
    AUTHORIZATION_DENIED = "authorization_denied"
    ENVIRONMENT_ACCESS_DENIED = "environment_access_denied"
    API_KEY_AUTH_REJECTED = "api_key_auth_rejected"
    WEBHOOK_SIGNATURE_REJECTED = "webhook_signature_rejected"
    RATE_LIMITED = "rate_limited"


REQUIRED_ENTERPRISE_EVENT_TYPES = frozenset(item.value for item in AuditEventType)


def normalize_event_type(value: str | AuditAction | AuditEventType) -> str:
    raw = getattr(value, "value", value)
    result = str(raw).strip().lower().replace(" ", "_")
    if not _EVENT_RE.fullmatch(result):
        raise ValueError("audit event type must be a stable lowercase identifier")
    return result


def storage_action(value: str | AuditAction | AuditEventType) -> AuditAction:
    """Preserve existing enum values and use the documented generic action otherwise."""
    if isinstance(value, AuditAction):
        return value
    event_type = normalize_event_type(value)
    try:
        return AuditAction(event_type)
    except ValueError:
        aliases = {
            "login": AuditAction.LOGIN_SUCCESS,
            "failed_login": AuditAction.LOGIN_FAILURE,
            "password_change": AuditAction.PASSWORD_CHANGED,
            "member_role_changed": AuditAction.ROLE_CHANGED,
            "security_setting_changed": AuditAction.SECURITY_SETTINGS_CHANGED,
            "billing_changed": AuditAction.BILLING_PLAN_CHANGED,
            "authorization_denied": AuditAction.AUTHZ_DENIED,
            "session_revoked": AuditAction.SESSION_REVOKED,
        }
        return aliases.get(event_type, AuditAction.GOVERNANCE_EVENT)
