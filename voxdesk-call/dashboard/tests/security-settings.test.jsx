/** Integration coverage for the dashboard's live enterprise-security surface. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import App from '../src/App'
import {
  EMPTY_OVERVIEW,
  installFetch,
  makeMe,
  OWNER_PERMISSIONS,
  sessionRoutes,
} from './harness'

const IDENTITY_PERMISSIONS = [
  ...OWNER_PERMISSIONS,
  'identity:read',
  'identity:write',
  'apikey:manage',
]

const IDENTITY_POLICY = {
  mfa_required: true,
  mfa_required_for_admins: true,
  privileged_reauth_required: true,
  sso_required: false,
  password_login_allowed: true,
  api_keys_allowed: true,
  service_accounts_allowed: true,
  scim_enabled: false,
  jit_provisioning_allowed: false,
  session_idle_minutes: 60,
  session_max_active: 8,
  refresh_token_days: 14,
  privileged_reauth_minutes: 10,
  allowed_email_domains: ['example.com'],
}

const SECURITY_POSTURE = {
  policy: IDENTITY_POLICY,
  roles: [
    { role: 'owner', level: 100, permissions: ['identity:read', 'identity:write', 'apikey:manage'] },
    { role: 'admin', level: 80, permissions: ['identity:read', 'identity:write', 'apikey:manage'] },
  ],
  inventory: {
    active_sessions: 2,
    active_api_keys: 1,
    revoked_api_keys: 1,
    active_service_account_credentials: 0,
    environments: 2,
  },
  controls: {
    audit_store: 'audit_logs',
    audit_tenant_scope: true,
    audit_environment_filtering: true,
    api_key_environment_binding_supported: true,
    mfa_feature_configured: true,
    sso_feature_configured: false,
    distributed_rate_limit_configured: true,
    rate_limit_enabled: true,
    trusted_host_allowlist_configured: true,
    forwarded_proxy_allowlist_configured: true,
    secret_redaction: 'structured-fields-and-credential-patterns',
    secret_store_backend: 'EncryptedDatabaseSecretStore',
  },
  audit: { latest_event_at: '2026-10-04T13:00:00+00:00' },
  reported_at: '2026-10-05T00:00:00+00:00',
}

const SECURITY_SESSIONS = {
  sessions: [
    {
      id: 'session-current',
      device: 'Firefox on Linux',
      auth_method: 'password+mfa',
      mfa_verified: true,
      current: true,
      status: 'active',
      last_seen_at: '2026-10-05T00:00:00+00:00',
      revoked_at: null,
      revoked_reason: '',
    },
    {
      id: 'session-revoked',
      device: 'Chrome on Android',
      auth_method: 'sso',
      mfa_verified: true,
      current: false,
      status: 'revoked',
      last_seen_at: '2026-10-04T00:00:00+00:00',
      revoked_at: '2026-10-04T01:00:00+00:00',
      revoked_reason: 'user_revoked',
    },
  ],
  current_session_id: 'session-current',
  limits: { idle_minutes: 60, max_active: 8, refresh_token_days: 14 },
}

const SECURITY_KEYS = [
  {
    id: 'key-1',
    name: 'Operations read-only',
    prefix: 'vdsk_01234567',
    scopes: ['call:read'],
    environment_id: 'environment-production',
    created_at: '2026-09-01T00:00:00+00:00',
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    revoked_reason: '',
  },
]

function securityBackend(me, extra = {}) {
  return {
    ...sessionRoutes(me),
    'GET /api/analytics/overview': { body: EMPTY_OVERVIEW },
    'GET /api/v1/enterprise-security/posture': { body: SECURITY_POSTURE },
    'GET /api/identity/policy': { body: IDENTITY_POLICY },
    'GET /api/sessions': { body: SECURITY_SESSIONS },
    'GET /api/api-keys': { body: SECURITY_KEYS },
    'GET /api/api-keys/scopes': {
      body: { grantable: ['call:read', 'lead:read'], all_scopes: ['call:read', 'lead:read'] },
    },
    'DELETE /api/sessions/session-current': { status: 204 },
    'POST /api/identity/reauth': { body: { ok: true } },
    'PATCH /api/identity/policy': { body: IDENTITY_POLICY },
    'POST /api/api-keys': {
      status: 201,
      body: {
        ...SECURITY_KEYS[0],
        id: 'key-created',
        secret: 'vdsk_abcdef0123456789_ONE_TIME_SECRET',
        warning: 'Copy this key now. It is stored as a hash and cannot be shown again.',
      },
    },
    ...extra,
  }
}

async function openSecuritySettings(me = makeMe(IDENTITY_PERMISSIONS), extra = {}) {
  window.location.hash = '#/security-settings'
  const fetched = installFetch(securityBackend(me, extra))
  const view = render(<App />)
  await screen.findByRole('heading', { name: /security & sessions/i, level: 1 })
  return { ...fetched, ...view }
}

describe('enterprise security settings page', () => {
  it('renders live tenant posture, current-user sessions, policy, and key inventory without certification claims', async () => {
    await openSecuritySettings()

    expect(await screen.findByText('Active sessions')).toBeInTheDocument()
    expect(screen.getByText('Active API keys')).toBeInTheDocument()
    expect(screen.getByText('Operations read-only')).toBeInTheDocument()
    expect(screen.getByText('Firefox on Linux')).toBeInTheDocument()
    const policySection = screen.getByRole('region', { name: /identity policy/i })
    expect(within(policySection).getAllByText('Require MFA for all members')[0].closest('.kv__row'))
      .toHaveTextContent('Enabled')
    expect(screen.getByText(/no certification or compliance status is inferred/i))
      .toBeInTheDocument()
    expect(screen.getByText(/do not represent an external audit or certification/i))
      .toBeInTheDocument()
    expect(screen.queryByText(/soc ?2 certified|iso 27001 certified/i)).toBeNull()
  })

  it('requires fresh reauthentication before creating a scoped one-time API key', async () => {
    const user = userEvent.setup()
    const { calls } = await openSecuritySettings()

    await user.type(screen.getByLabelText('Name'), 'Nightly reporting')
    await user.type(
      screen.getByLabelText(/permission scopes/i),
      'call:read lead:read',
    )
    await user.type(
      screen.getByLabelText(/environment uuid/i),
      'environment-production',
    )
    await user.type(
      screen.getByPlaceholderText(/password for fresh reauthentication/i),
      'fresh-password',
    )
    await user.click(screen.getByRole('button', { name: /reauthenticate & create key/i }))

    expect(await screen.findByText('vdsk_abcdef0123456789_ONE_TIME_SECRET'))
      .toBeInTheDocument()
    await waitFor(() => expect(calls.some((call) => call.path === '/api/api-keys')).toBe(true))

    const reauthIndex = calls.findIndex((call) => call.path === '/api/identity/reauth')
    const createIndex = calls.findIndex((call) => call.method === 'POST' && call.path === '/api/api-keys')
    expect(reauthIndex).toBeGreaterThan(-1)
    expect(createIndex).toBeGreaterThan(reauthIndex)
    expect(JSON.parse(calls[reauthIndex].options.body)).toEqual({ password: 'fresh-password' })
    expect(JSON.parse(calls[createIndex].options.body)).toEqual({
      name: 'Nightly reporting',
      scopes: ['call:read', 'lead:read'],
      environment_id: 'environment-production',
    })
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('keeps session history restricted to the current principal and revokes through the session API', async () => {
    const user = userEvent.setup()
    const { calls } = await openSecuritySettings()

    const sessions = screen.getByRole('region', { name: /your sessions/i })
    expect(within(sessions).getByText('Firefox on Linux')).toBeInTheDocument()
    expect(within(sessions).getByText('Chrome on Android')).toBeInTheDocument()
    await user.click(within(sessions).getAllByRole('button', { name: /revoke/i })[0])

    await waitFor(() => expect(calls.some(
      (call) => call.method === 'DELETE' && call.path === '/api/sessions/session-current',
    )).toBe(true))
    expect(within(sessions).getByText(/other members’ sessions are not shown/i))
      .toBeInTheDocument()
  })
})
