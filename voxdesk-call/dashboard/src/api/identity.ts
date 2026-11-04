/**
 * dashboard/src/api/identity.ts
 * Enterprise identity surface: SSO connections, SCIM credentials, API keys,
 * service accounts and verified email domains.
 *
 * Route sources (verified against app/main.py mounts and app/api/*.py):
 *   app/api/sso_routes.py            admin_router → `/api/sso`
 *   app/api/scim_routes.py           admin_router → `/api/scim`
 *   app/api/api_key_routes.py        → `/api/api-keys`
 *   app/api/service_account_routes.py→ `/api/service-accounts`
 *   app/api/domain_routes.py         → `/api/domains`
 *
 * Secret material (`ApiKeyCreatedOut.secret`, `CredentialCreatedOut.token`) is
 * returned exactly once by the backend; the types below keep that explicit so a
 * page cannot accidentally render a masked value as if it were the secret.
 */
import { client } from './client';

export interface SsoConnection {
  id: string;
  name: string;
  slug: string;
  protocol: string;
  status: string;
  issuer?: string;
  client_id?: string;
  has_client_secret?: boolean;
  idp_sso_url?: string;
  idp_slo_url?: string;
  idp_entity_id?: string;
  metadata_url?: string;
  acs_url?: string;
  sp_entity_id?: string;
  redirect_uri?: string;
  scopes?: string;
  subject_claim?: string;
  email_claim?: string;
  name_claim?: string;
  role_claim?: string;
  group_claim?: string;
  role_mapping?: Record<string, unknown>;
  group_mapping?: Record<string, unknown>;
  default_role?: string;
  deny_unmapped_roles?: boolean;
  jit_enabled?: boolean;
  require_verified_email?: boolean;
  require_signed_assertions?: boolean;
  allow_account_linking?: boolean;
  use_pkce?: boolean;
  active_certificates?: number;
  last_login_at?: string | null;
  created_at?: string | null;
}

export interface SsoConnectionInput {
  name: string;
  protocol?: string;
  slug?: string;
  issuer?: string;
  client_id?: string;
  client_secret?: string;
  idp_sso_url?: string;
  idp_slo_url?: string;
  idp_entity_id?: string;
  idp_metadata?: string;
  metadata_url?: string;
  acs_url?: string;
  sp_entity_id?: string;
  redirect_uri?: string;
  scopes?: string;
  subject_claim?: string;
  email_claim?: string;
  name_claim?: string;
  role_claim?: string;
  group_claim?: string;
  default_role?: string;
  deny_unmapped_roles?: boolean;
  jit_enabled?: boolean;
  require_verified_email?: boolean;
  require_signed_assertions?: boolean;
  allow_account_linking?: boolean;
  use_pkce?: boolean;
}

export interface SsoCertificate {
  id: string;
  connection_id?: string;
  fingerprint?: string;
  is_active?: boolean;
  not_after?: string | null;
  created_at?: string | null;
}

export interface SsoTestResult {
  ok?: boolean;
  status?: string;
  detail?: string;
  [key: string]: unknown;
}

export interface ScimCredential {
  id: string;
  prefix: string;
  scopes: string[];
  label?: string;
  connection_id?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

export interface ScimCredentialCreated extends ScimCredential {
  /** Returned once, at creation/rotation. */
  token: string;
  warning?: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at?: string | null;
  expires_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
  revoked_reason?: string;
  environment_id?: string | null;
  service_account_id?: string | null;
  owner_user_id?: string | null;
  rotated_from_id?: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  /** Returned once, at creation/rotation. */
  secret: string;
  warning?: string;
}

export interface ServiceAccount {
  id: string;
  name: string;
  scopes: string[];
  enabled: boolean;
  description?: string;
  credential_count?: number;
  emergency_disabled?: boolean;
  disabled_reason?: string;
  scope_summary?: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  owner_user_id?: string | null;
  created_at?: string | null;
}

export interface Domain {
  id: string;
  domain: string;
  verified: boolean;
  verification_record_name?: string;
  verification_status?: string;
  enforcement?: string;
  block_password_login?: boolean;
  failed_attempts?: number;
  sso_connection_id?: string | null;
  verified_at?: string | null;
  created_at?: string | null;
}

export interface DomainCreated extends Domain {
  verification_record_value: string;
}

export interface DomainVerification {
  verified: boolean;
  status: string;
  detail: string;
  record_name: string;
  records_seen: number;
}

/* -------------------------------------------------------------------- SSO */

export function listSsoConnections(): Promise<SsoConnection[]> {
  return client.get<SsoConnection[]>('/api/sso/connections');
}

export function createSsoConnection(input: SsoConnectionInput): Promise<SsoConnection> {
  return client.post<SsoConnection>('/api/sso/connections', input);
}

export function updateSsoConnection(connectionId: string, input: SsoConnectionInput): Promise<SsoConnection> {
  return client.patch<SsoConnection>(`/api/sso/connections/${encodeURIComponent(connectionId)}`, input);
}

export function deleteSsoConnection(connectionId: string): Promise<null> {
  return client.del<null>(`/api/sso/connections/${encodeURIComponent(connectionId)}`);
}

export function setSsoConnectionStatus(connectionId: string, status: string): Promise<SsoConnection> {
  return client.post<SsoConnection>(`/api/sso/connections/${encodeURIComponent(connectionId)}/status`, { status });
}

export function testSsoConnection(connectionId: string): Promise<SsoTestResult> {
  return client.post<SsoTestResult>(`/api/sso/connections/${encodeURIComponent(connectionId)}/test`);
}

export function listSsoCertificates(connectionId: string): Promise<SsoCertificate[]> {
  return client.get<SsoCertificate[]>(`/api/sso/connections/${encodeURIComponent(connectionId)}/certificates`);
}

export function addSsoCertificate(connectionId: string, certificate: string, makeActive = true): Promise<SsoCertificate> {
  return client.post<SsoCertificate>(`/api/sso/connections/${encodeURIComponent(connectionId)}/certificates`, {
    certificate,
    make_active: makeActive,
  });
}

export function retireSsoCertificate(connectionId: string, certificateId: string): Promise<null> {
  return client.del<null>(
    `/api/sso/connections/${encodeURIComponent(connectionId)}/certificates/${encodeURIComponent(certificateId)}`,
  );
}

export function setSsoMappings(
  connectionId: string,
  mappings: { role_mapping?: Record<string, unknown>; group_mapping?: Record<string, unknown>; default_role?: string | null; deny_unmapped_roles?: boolean },
): Promise<SsoConnection> {
  return client.put<SsoConnection>(`/api/sso/connections/${encodeURIComponent(connectionId)}/mappings`, mappings);
}

/* ------------------------------------------------------------------- SCIM */

export function listScimCredentials(includeRevoked = false): Promise<ScimCredential[]> {
  return client.get<ScimCredential[]>(`/api/scim/credentials?include_revoked=${includeRevoked ? 'true' : 'false'}`);
}

export function createScimCredential(input: { label?: string; scopes?: string[]; expires_in_days?: number | null } = {}): Promise<ScimCredentialCreated> {
  return client.post<ScimCredentialCreated>('/api/scim/credentials', input);
}

export function rotateScimCredential(credentialId: string): Promise<ScimCredentialCreated> {
  return client.post<ScimCredentialCreated>(`/api/scim/credentials/${encodeURIComponent(credentialId)}/rotate`);
}

export function revokeScimCredential(credentialId: string): Promise<null> {
  return client.del<null>(`/api/scim/credentials/${encodeURIComponent(credentialId)}`);
}

/* ---------------------------------------------------------------- API keys */

export function listApiKeys(params: { includeRevoked?: boolean; mineOnly?: boolean; limit?: number; offset?: number } = {}): Promise<ApiKey[]> {
  const qs = new URLSearchParams();
  qs.set('include_revoked', params.includeRevoked ? 'true' : 'false');
  qs.set('mine_only', params.mineOnly ? 'true' : 'false');
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
  return client.get<ApiKey[]>(`/api/api-keys?${qs.toString()}`);
}

export function listApiKeyScopes(): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>('/api/api-keys/scopes');
}

export function createApiKey(input: { name: string; scopes?: string[]; expires_in_days?: number | null; environment_id?: string | null }): Promise<ApiKeyCreated> {
  return client.post<ApiKeyCreated>('/api/api-keys', input);
}

export function rotateApiKey(keyId: string, expiresInDays?: number | null): Promise<ApiKeyCreated> {
  const suffix = expiresInDays === undefined || expiresInDays === null ? '' : `?expires_in_days=${encodeURIComponent(String(expiresInDays))}`;
  return client.post<ApiKeyCreated>(`/api/api-keys/${encodeURIComponent(keyId)}/rotate${suffix}`);
}

export function revokeApiKey(keyId: string): Promise<null> {
  return client.del<null>(`/api/api-keys/${encodeURIComponent(keyId)}`);
}

/* --------------------------------------------------------- service accounts */

export function listServiceAccounts(): Promise<ServiceAccount[]> {
  return client.get<ServiceAccount[]>('/api/service-accounts');
}

export function createServiceAccount(input: { name: string; description?: string; scopes?: string[]; expires_in_days?: number | null }): Promise<ServiceAccount> {
  return client.post<ServiceAccount>('/api/service-accounts', input);
}

export function updateServiceAccount(accountId: string, patch: Record<string, unknown>): Promise<ServiceAccount> {
  return client.patch<ServiceAccount>(`/api/service-accounts/${encodeURIComponent(accountId)}`, patch);
}

export function deleteServiceAccount(accountId: string): Promise<null> {
  return client.del<null>(`/api/service-accounts/${encodeURIComponent(accountId)}`);
}

export function disableServiceAccount(accountId: string): Promise<ServiceAccount> {
  return client.post<ServiceAccount>(`/api/service-accounts/${encodeURIComponent(accountId)}/disable`);
}

export function enableServiceAccount(accountId: string): Promise<ServiceAccount> {
  return client.post<ServiceAccount>(`/api/service-accounts/${encodeURIComponent(accountId)}/enable`);
}

export function listServiceAccountCredentials(accountId: string, includeRevoked = false): Promise<ScimCredential[]> {
  return client.get<ScimCredential[]>(
    `/api/service-accounts/${encodeURIComponent(accountId)}/credentials?include_revoked=${includeRevoked ? 'true' : 'false'}`,
  );
}

export function createServiceAccountCredential(accountId: string, input: { label?: string; scopes?: string[] } = {}): Promise<ScimCredentialCreated> {
  return client.post<ScimCredentialCreated>(`/api/service-accounts/${encodeURIComponent(accountId)}/credentials`, input);
}

/* ------------------------------------------------------------------ domains */

export function listDomains(): Promise<Domain[]> {
  return client.get<Domain[]>('/api/domains');
}

export function addDomain(domain: string): Promise<DomainCreated> {
  return client.post<DomainCreated>('/api/domains', { domain });
}

export function updateDomain(domainId: string, patch: { enforcement?: string | null; block_password_login?: boolean | null; sso_connection_id?: string | null }): Promise<Domain> {
  return client.patch<Domain>(`/api/domains/${encodeURIComponent(domainId)}`, patch);
}

export function removeDomain(domainId: string): Promise<null> {
  return client.del<null>(`/api/domains/${encodeURIComponent(domainId)}`);
}

export function challengeDomain(domainId: string): Promise<DomainCreated> {
  return client.post<DomainCreated>(`/api/domains/${encodeURIComponent(domainId)}/challenge`);
}

export function verifyDomain(domainId: string): Promise<DomainVerification> {
  return client.post<DomainVerification>(`/api/domains/${encodeURIComponent(domainId)}/verify`);
}
