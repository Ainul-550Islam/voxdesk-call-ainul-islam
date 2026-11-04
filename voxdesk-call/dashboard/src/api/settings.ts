/**
 * dashboard/src/api/settings.ts
 * Workspace/profile/security settings.
 *
 * Route sources (verified):
 *   app/api/tenant_security_routes.py → GET  /api/tenants/{tenant_id}/settings
 *                                       (app/tenancy/settings.py::public_view)
 *       GET /api/tenants/{tenant_id}/security/posture
 *   app/api/tenant_admin_routes.py    → PATCH /api/tenants/{tenant_id}/profile
 *       body `TenantProfileUpdate` = `{name}` only; the response is `TenantAdminOut`
 *   app/api/identity_routes.py        → GET/PATCH /api/identity/policy, GET /api/identity/status
 *   app/api/mfa_routes.py             → GET /api/mfa/status
 *   app/api/session_routes.py         → GET /api/sessions, POST /api/sessions/revoke-others
 *
 * The settings view is an allowlist: `app/tenancy/settings.py` withholds secret
 * and routing fields by construction (`SECRET_FIELDS`/`WITHHELD_FIELDS`), so this
 * client cannot request them and the page cannot render them.
 */
import { client } from './client';

/** `app/tenancy/settings.py::_PUBLIC` — the only keys the server returns. */
export interface WorkspaceSettings {
  name: string;
  industry: string | null;
  timezone: string | null;
  language: string | null;
  lifecycle_status: string;
  plan: string | null;
  sms_enabled: boolean;
  whatsapp_enabled: boolean;
  outbound_enabled: boolean;
  record_calls: boolean;
  ivr_enabled: boolean;
  humanize: boolean;
}

export interface TenantProfile {
  id: string;
  name: string;
  organization_id: string;
  lifecycle_status: string;
  is_active: boolean;
  twilio_number: string;
}

/** `PolicyOut` from app/api/identity_routes.py. */
export interface IdentityPolicy {
  identity_policy_id: string | null;
  is_default: boolean;
  mfa_required: boolean;
  mfa_required_for_admins: boolean;
  privileged_reauth_required: boolean;
  privileged_reauth_minutes: number;
  sso_required: boolean;
  password_login_allowed: boolean;
  api_keys_allowed: boolean;
  service_accounts_allowed: boolean;
  scim_enabled: boolean;
  jit_provisioning_allowed: boolean;
  session_idle_minutes: number;
  session_max_active: number;
  refresh_token_days: number;
  allowed_email_domains: string[];
}

/**
 * `PolicyIn` refuses unknown keys (`extra="forbid"`) — every field is optional
 * and only the ones the operator changed are sent.
 */
export type IdentityPolicyPatch = Partial<
  Omit<IdentityPolicy, 'identity_policy_id' | 'is_default'>
>;

/** `MFAStatusOut` from app/api/mfa_routes.py. */
export interface MfaStatus {
  enrolled: boolean;
  pending: boolean;
  factor_id?: string | null;
  confirmed_at?: string | null;
  last_used_at?: string | null;
  recovery_codes_remaining: number;
  recovery_codes_total: number;
  recovery_codes_low: boolean;
  required_by_policy: boolean;
  required_for_admins: boolean;
  enabled_on_deployment: boolean;
  issuer: string;
  digits: number;
  period_seconds: number;
}

/**
 * `StatusOut` from app/api/identity_routes.py — what *this person* and this
 * deployment can do. `features` is `identity_features(policy)`, a flat bool map.
 */
export interface IdentityStatus {
  features: Record<string, boolean>;
  policy: IdentityPolicy;
  mfa: MfaStatus;
  session: {
    session_id?: string | null;
    auth_method?: string;
    mfa_verified?: boolean;
    mfa_verified_at?: string | null;
    password_confirmed_at?: string | null;
    created_at?: string | null;
    idle_expires_at?: string | null;
    expires_at?: string | null;
    device?: string;
  };
  email_verified: boolean;
  reauth: Record<string, unknown>;
}

/** `SessionOut` from app/api/security_session_routes.py. */
export interface SessionRecord {
  id: string;
  device: string;
  user_agent: string;
  ip_address: string;
  auth_method: string;
  mfa_verified: boolean;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  idle_expires_at: string;
  expires_at: string;
  status: string;
  revoked_at?: string | null;
  revoked_reason: string;
}

/** `SessionListOut` — the list endpoint returns an envelope, not an array. */
export interface SessionList {
  sessions: SessionRecord[];
  current_session_id?: string | null;
  limits: { idle_minutes?: number; max_active?: number; refresh_token_days?: number };
}

/**
 * `GET /api/tenants/{id}/security/posture` — the server-resolved hierarchy.
 * The client never supplies the tenant; it is echoed back for confirmation.
 */
export interface SecurityPosture {
  organization_id: string;
  tenant_id: string;
  environment_id?: string | null;
  default_environment_id?: string | null;
  auth_method: string;
  is_machine: boolean;
  client_tenant_override: boolean;
}

export function getWorkspaceSettings(tenantId: string): Promise<WorkspaceSettings> {
  return client.get<WorkspaceSettings>(`/api/tenants/${encodeURIComponent(tenantId)}/settings`);
}

export function updateWorkspaceProfile(tenantId: string, name: string): Promise<TenantProfile> {
  return client.patch<TenantProfile>(`/api/tenants/${encodeURIComponent(tenantId)}/profile`, { name });
}

export function getSecurityPosture(tenantId: string): Promise<SecurityPosture> {
  return client.get<SecurityPosture>(`/api/tenants/${encodeURIComponent(tenantId)}/security/posture`);
}

export function getIdentityPolicy(): Promise<IdentityPolicy> {
  return client.get<IdentityPolicy>('/api/identity/policy');
}

export function updateIdentityPolicy(patch: IdentityPolicyPatch): Promise<IdentityPolicy> {
  return client.patch<IdentityPolicy>('/api/identity/policy', patch);
}

export function getIdentityStatus(): Promise<IdentityStatus> {
  return client.get<IdentityStatus>('/api/identity/status');
}

export function getMfaStatus(): Promise<MfaStatus> {
  return client.get<MfaStatus>('/api/mfa/status');
}

/**
 * `GET /api/sessions` returns `{sessions, current_session_id, limits}`.
 * `includeRevoked` maps to `?include_revoked=` (bounded recent history).
 */
export function listSessions(includeRevoked = false): Promise<SessionList> {
  return client.get<SessionList>(`/api/sessions${includeRevoked ? '?include_revoked=true' : ''}`);
}

/** `DELETE /api/sessions/{id}` — sign out one device (204, no body). */
export function revokeSession(sessionId: string): Promise<null> {
  return client.del<null>(`/api/sessions/${encodeURIComponent(sessionId)}`);
}

/** `POST /api/sessions/revoke-others` → `{revoked: number}`. */
export function revokeOtherSessions(): Promise<{ revoked: number }> {
  return client.post<{ revoked: number }>('/api/sessions/revoke-others');
}
