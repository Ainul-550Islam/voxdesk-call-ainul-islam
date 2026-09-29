"""
The permission vocabulary.

Every capability in the product is named exactly once here. Route code asks
for a `Permission`, never for a role string -- so adding a role, or moving a
capability between roles, is a one-line change in `rbac.ROLE_PERMISSIONS`
instead of a repository-wide grep for `if user.role == "admin"`.
"""
from __future__ import annotations

import enum


def is_read_permission(permission: "Permission") -> bool:
    """True for a permission that does not change state.

    Suspended membership may keep these and must lose everything else. The
    test is the permission id, not a second role table.
    """
    value = permission.value
    return value.endswith(":read") or value.endswith(":read_all")


class Permission(str, enum.Enum):
    # ---- tenant configuration -------------------------------------------
    TENANT_READ = "tenant:read"
    TENANT_UPDATE = "tenant:update"          # voice tuning, IVR, business hours
    TENANT_CREATE = "tenant:create"          # provisioning a brand new business
    TENANT_DELETE = "tenant:delete"

    # ---- team -----------------------------------------------------------
    USER_READ = "user:read"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"              # rename, deactivate, reactivate
    USER_ROLE_CHANGE = "user:role_change"
    USER_DELETE = "user:delete"

    # ---- operational data -----------------------------------------------
    CALL_READ = "call:read"
    CALL_READ_ALL = "call:read_all"          # not just calls assigned to me
    TRANSCRIPT_READ = "transcript:read"
    RECORDING_READ = "recording:read"

    LEAD_READ = "lead:read"
    LEAD_CREATE = "lead:create"
    LEAD_UPDATE = "lead:update"
    LEAD_DELETE = "lead:delete"

    APPOINTMENT_READ = "appointment:read"
    APPOINTMENT_WRITE = "appointment:write"

    CAMPAIGN_READ = "campaign:read"
    CAMPAIGN_WRITE = "campaign:write"
    CAMPAIGN_RUN = "campaign:run"            # spends real money on calls

    # ---- knowledge base / RAG -------------------------------------------
    KNOWLEDGE_READ = "knowledge:read"        # list/inspect documents
    KNOWLEDGE_WRITE = "knowledge:write"      # upload, reindex, archive
    KNOWLEDGE_DELETE = "knowledge:delete"    # permanent removal

    # ---- reporting -------------------------------------------------------
    ANALYTICS_READ = "analytics:read"
    AUDIT_READ = "audit:read"

    # ---- sensitive -------------------------------------------------------
    INTEGRATION_READ = "integration:read"    # CRM / calendar wiring
    INTEGRATION_WRITE = "integration:write"
    # Firing a sync spends the tenant's provider rate limit and writes to
    # their CRM, so it is separated from merely reading the configuration.
    INTEGRATION_SYNC = "integration:sync"
    COMPLIANCE_READ = "compliance:read"
    COMPLIANCE_WRITE = "compliance:write"    # A2P registration, DNC handling
    SECURITY_SETTINGS = "security:settings"
    BILLING_READ = "billing:read"
    BILLING_WRITE = "billing:write"

    # ---- enterprise identity (STEP 18) ----------------------------------
    # Named in the same `resource:action` vocabulary as everything above, so an
    # API key scope is simply the permission string and there is exactly one
    # list to review. `identity:read` exposes the tenant's security posture
    # (which connections exist, which sessions are live) but never a secret;
    # `identity:write` is the one that changes who can log in and how.
    IDENTITY_READ = "identity:read"
    IDENTITY_WRITE = "identity:write"
    API_KEY_MANAGE = "apikey:manage"                 # any key in the tenant
    SERVICE_ACCOUNT_MANAGE = "service_account:manage"  # machine identities

    # Contact-center ACD. These name queue, skill, presence, routing and
    # supervisor actions. They do not create a second role table.
    QUEUE_READ = "queue:read"
    QUEUE_WRITE = "queue:write"
    SKILL_READ = "skill:read"
    SKILL_WRITE = "skill:write"
    AGENT_STATE_READ = "agent_state:read"
    AGENT_STATE_WRITE = "agent_state:write"
    ROUTING_READ = "routing:read"
    ROUTING_WRITE = "routing:write"
    SUPERVISOR_READ = "supervisor:read"
    SUPERVISOR_WRITE = "supervisor:write"

    # QA and conversation intelligence. These name scorecards, reviews and
    # exports. They do not create a second role table or a live-media path.
    QA_READ = "qa:read"
    QA_WRITE = "qa:write"
    QA_REVIEW = "qa:review"
    QA_FINALIZE = "qa:finalize"

    # Prompt 3 controlled extension surfaces. These remain part of the existing
    # role/scope policy; they do not introduce a second authorization model.
    CONNECTOR_READ = "connector:read"
    CONNECTOR_WRITE = "connector:write"
    CONNECTOR_EXECUTE = "connector:execute"
    MCP_READ = "mcp:read"
    MCP_WRITE = "mcp:write"
    MCP_EXECUTE = "mcp:execute"
    API_TOOL_READ = "api_tool:read"
    API_TOOL_WRITE = "api_tool:write"
    API_TOOL_EXECUTE = "api_tool:execute"
    EMAIL_SEND = "email:send"
    APPROVAL_READ = "approval:read"
    APPROVAL_WRITE = "approval:write"
    SECRET_MANAGE = "secret:manage"

    # Enterprise AI governance. These extend the existing permission vocabulary;
    # routes continue to use the shared dependency and role tables.
    GOVERNANCE_READ = "governance:read"
    GOVERNANCE_WRITE = "governance:write"
    GOVERNANCE_APPROVE = "governance:approve"
    GOVERNANCE_MODEL_MANAGE = "governance:model_manage"
    GOVERNANCE_EVIDENCE_EXPORT = "governance:evidence_export"

    # Voice runtime resources. These extend the existing RBAC vocabulary and
    # govern provider-backed profiles and clone jobs without exposing secrets.
    VOICE_PROFILE_READ = "voice_profile:read"
    VOICE_PROFILE_WRITE = "voice_profile:write"
    VOICE_CLONE_READ = "voice_clone:read"
    VOICE_CLONE_WRITE = "voice_clone:write"


# Convenience bundles used when composing roles. Kept here so `rbac.py` reads
# as a policy document rather than a wall of enum members.
READ_ONLY_OPERATIONAL: frozenset[Permission] = frozenset({
    Permission.TENANT_READ,
    Permission.CALL_READ,
    Permission.CALL_READ_ALL,
    Permission.TRANSCRIPT_READ,
    Permission.LEAD_READ,
    Permission.APPOINTMENT_READ,
    Permission.CAMPAIGN_READ,
    Permission.KNOWLEDGE_READ,
    Permission.ANALYTICS_READ,
})

OPERATIONAL_WRITE: frozenset[Permission] = frozenset({
    Permission.LEAD_CREATE,
    Permission.LEAD_UPDATE,
    Permission.APPOINTMENT_WRITE,
    Permission.CAMPAIGN_WRITE,
    Permission.CAMPAIGN_RUN,
    Permission.RECORDING_READ,
})

TEAM_MANAGEMENT: frozenset[Permission] = frozenset({
    Permission.USER_READ,
    Permission.USER_CREATE,
    Permission.USER_UPDATE,
    Permission.USER_ROLE_CHANGE,
})