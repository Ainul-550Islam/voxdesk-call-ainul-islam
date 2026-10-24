"""HIPAA technical safeguards: PHI tagging, minimum necessary, break-glass, logoff.

The modules here are deliberately small and single-purpose so a reader can
answer "what does the product do when PHI is involved" from four files:

* :mod:`phi_tagging` — which columns carry PHI, and what happens to them on the
  way out of the system;
* :mod:`minimum_necessary` — which fields a given role may see on a given
  surface;
* :mod:`break_glass` — time-boxed, dual-approved emergency access;
* :mod:`session_policy` — session limits and re-authentication for privileged
  actions.

These are technical safeguards only. Nothing here asserts that VoxDesk is
HIPAA-compliant; that determination depends on agreements, policies and
operational practice outside the code.
"""

from app.compliance_v2.hipaa.session_policy import (
    HIPAA_MAX_ABSOLUTE_MINUTES,
    HIPAA_MAX_IDLE_MINUTES,
    ReauthRequired,
    SessionLimits,
    audit_reauth_denied,
    limits_for,
    require_recent_auth,
    sweep,
)
from app.compliance_v2.hipaa.break_glass import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REVOKED,
    ApprovalRequired,
    BreakGlassError,
    GrantNotFound,
    active_grants,
    approve_grant,
    authorize_read,
    expire_due,
    request_grant,
    revoke_grant,
)
from app.compliance_v2.hipaa.minimum_necessary import (
    SURFACE_EXPORT,
    SURFACE_REPLAY,
    SURFACE_TRANSCRIPT,
    allowlist_for,
    filter_record,
    filter_records,
    surface_for_path,
)
from app.compliance_v2.hipaa.phi_tagging import (
    PHI_FIELDS,
    REDACTION,
    PhiPolicyView,
    load_policy,
    propagate_to_webhook,
    redact_export_records,
    set_policy,
    tagged_columns_document,
)

__all__ = [
    "HIPAA_MAX_ABSOLUTE_MINUTES",
    "HIPAA_MAX_IDLE_MINUTES",
    "PHI_FIELDS",
    "REDACTION",
    "STATUS_ACTIVE",
    "STATUS_EXPIRED",
    "STATUS_PENDING",
    "STATUS_REVOKED",
    "SURFACE_EXPORT",
    "SURFACE_REPLAY",
    "SURFACE_TRANSCRIPT",
    "ApprovalRequired",
    "BreakGlassError",
    "GrantNotFound",
    "PhiPolicyView",
    "ReauthRequired",
    "SessionLimits",
    "active_grants",
    "allowlist_for",
    "approve_grant",
    "audit_reauth_denied",
    "authorize_read",
    "expire_due",
    "filter_record",
    "filter_records",
    "limits_for",
    "load_policy",
    "propagate_to_webhook",
    "redact_export_records",
    "request_grant",
    "require_recent_auth",
    "revoke_grant",
    "set_policy",
    "surface_for_path",
    "sweep",
    "tagged_columns_document",
]
