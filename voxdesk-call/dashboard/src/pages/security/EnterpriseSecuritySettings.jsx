import { useEffect, useMemo, useState } from 'react'

import {
  Alert,
  AsyncSection,
  DataTable,
  EmptyState,
  StatCard,
} from '../../components/ui'
import {
  createSecurityApiKey,
  getApiKeyScopes,
  getEnterpriseSecurityPosture,
  getIdentityPolicy,
  listSecurityApiKeys,
  listSecuritySessions,
  reauthenticateIdentity,
  revokeSecurityApiKey,
  revokeSecuritySession,
  rotateSecurityApiKey,
  updateIdentityPolicy,
} from '../../lib/api'
import { formatDateTime, formatNumber, humanise } from '../../lib/format'
import { useApi } from '../../lib/hooks'
import { PERMISSIONS as P } from '../../lib/permissions'

const POLICY_DEFAULTS = {
  mfa_required: false,
  mfa_required_for_admins: false,
  password_login_allowed: true,
  api_keys_allowed: true,
  service_accounts_allowed: true,
  session_idle_minutes: 720,
  session_max_active: 20,
  refresh_token_days: 14,
}

const POLICY_TOGGLES = [
  ['mfa_required', 'Require MFA for all members'],
  ['mfa_required_for_admins', 'Require MFA for administrators'],
  ['password_login_allowed', 'Allow password sign-in'],
  ['api_keys_allowed', 'Allow API keys'],
  ['service_accounts_allowed', 'Allow service accounts'],
]

function presentDate(value, timezone) {
  return value ? formatDateTime(value, timezone) : '—'
}

function asMessage(error) {
  return error?.message || 'The request could not be completed.'
}

export default function EnterpriseSecuritySettings({ me, can }) {
  const timezone = me?.tenant?.timezone || 'UTC'
  const identityRead = can(P.IDENTITY_READ)
  const identityWrite = can(P.IDENTITY_WRITE)
  const apiKeyManage = can(P.API_KEY_MANAGE)

  const posture = useApi(
    () => (identityRead ? getEnterpriseSecurityPosture() : Promise.resolve(null)),
    [identityRead],
  )
  const policy = useApi(
    () => (identityRead ? getIdentityPolicy() : Promise.resolve(null)),
    [identityRead],
  )
  const sessions = useApi(() => listSecuritySessions(true), [])
  const apiKeys = useApi(
    () => (apiKeyManage ? listSecurityApiKeys(true) : Promise.resolve([])),
    [apiKeyManage],
  )
  const scopes = useApi(
    () => (apiKeyManage ? getApiKeyScopes() : Promise.resolve({ grantable: [] })),
    [apiKeyManage],
  )

  const [policyDraft, setPolicyDraft] = useState(POLICY_DEFAULTS)
  const [reauthPassword, setReauthPassword] = useState('')
  const [reauthCode, setReauthCode] = useState('')
  const [keyName, setKeyName] = useState('')
  const [keyScopes, setKeyScopes] = useState('')
  const [keyEnvironment, setKeyEnvironment] = useState('')
  const [issuedSecret, setIssuedSecret] = useState('')
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const [pending, setPending] = useState(false)

  useEffect(() => {
    if (!policy.data) return
    setPolicyDraft({
      ...POLICY_DEFAULTS,
      ...Object.fromEntries(
        Object.keys(POLICY_DEFAULTS).map((key) => [key, policy.data[key] ?? POLICY_DEFAULTS[key]]),
      ),
    })
  }, [policy.data])

  const postureRoles = posture.data?.roles ?? []
  const sessionRows = sessions.data?.sessions ?? []
  const keyRows = apiKeys.data ?? []
  const grantableScopes = scopes.data?.grantable ?? []

  const sessionColumns = useMemo(() => [
    {
      key: 'device',
      header: 'Device',
      render: (row) => (
        <div>
          <strong>{row.device || 'Unknown device'}</strong>
          <div className="muted" style={{ fontSize: 12 }}>{row.auth_method || '—'}</div>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <span className={`badge badge--${row.status === 'active' ? 'ok' : 'muted'}`}>
          {humanise(row.status || 'unknown')}{row.current ? ' · current' : ''}
        </span>
      ),
    },
    {
      key: 'mfa',
      header: 'MFA',
      render: (row) => row.mfa_verified ? 'Verified' : 'Not verified',
    },
    {
      key: 'last_seen',
      header: 'Last seen',
      render: (row) => presentDate(row.last_seen_at, timezone),
    },
    {
      key: 'ended',
      header: 'Ended',
      render: (row) => row.revoked_at
        ? `${presentDate(row.revoked_at, timezone)}${row.revoked_reason ? ` · ${humanise(row.revoked_reason)}` : ''}`
        : '—',
    },
    {
      key: 'action',
      header: 'Action',
      render: (row) => row.status === 'active' ? (
        <button
          type="button"
          className="btn btn--small"
          disabled={pending}
          onClick={() => runSessionRevocation(row)}
        >
          Revoke
        </button>
      ) : <span className="muted">—</span>,
    },
  ], [timezone, pending, runSessionRevocation])

  const keyColumns = useMemo(() => [
    {
      key: 'name',
      header: 'Key',
      render: (row) => (
        <div>
          <strong>{row.name}</strong>
          <div className="muted" style={{ fontSize: 12 }}>{row.prefix}</div>
        </div>
      ),
    },
    {
      key: 'environment',
      header: 'Environment',
      render: (row) => row.environment_id || 'Tenant-wide',
    },
    {
      key: 'scopes',
      header: 'Scopes',
      render: (row) => (row.scopes ?? []).length
        ? (row.scopes ?? []).join(', ')
        : 'No permissions',
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => row.revoked_at
        ? <span className="badge badge--muted">Revoked</span>
        : <span className="badge badge--ok">Active</span>,
    },
    {
      key: 'last_used',
      header: 'Last used',
      render: (row) => presentDate(row.last_used_at, timezone),
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (row) => row.revoked_at ? (
        <span className="muted">—</span>
      ) : (
        <div className="row" style={{ gap: 8 }}>
          <button
            type="button"
            className="btn btn--small"
            disabled={pending}
            onClick={() => runKeyRotation(row)}
          >
            Rotate
          </button>
          <button
            type="button"
            className="btn btn--small"
            disabled={pending}
            onClick={() => runKeyRevocation(row)}
          >
            Revoke
          </button>
        </div>
      ),
    },
  ], [timezone, pending, runKeyRotation, runKeyRevocation])

  async function withFreshReauthentication(action) {
    if (!reauthPassword && !reauthCode) {
      throw new Error('Enter your password or an MFA code before this privileged action.')
    }
    await reauthenticateIdentity({
      password: reauthPassword || undefined,
      code: reauthCode || undefined,
    })
    setReauthPassword('')
    setReauthCode('')
    return action()
  }

  async function perform(label, action) {
    setPending(true)
    setNotice('')
    setActionError('')
    try {
      await action()
      setNotice(label)
    } catch (error) {
      setActionError(asMessage(error))
    } finally {
      setPending(false)
    }
  }

  function runSessionRevocation(row) {
    perform(
      row.current ? 'The current session was revoked.' : 'The session was revoked.',
      async () => {
        await revokeSecuritySession(row.id)
        await sessions.reload()
      },
    )
  }

  function runKeyRotation(row) {
    perform('The API key was rotated. Copy the new secret below; it will not be shown again.', async () => {
      const issued = await withFreshReauthentication(() => rotateSecurityApiKey(row.id))
      setIssuedSecret(issued?.secret || '')
      await apiKeys.reload()
    })
  }

  function runKeyRevocation(row) {
    perform('The API key was revoked.', async () => {
      await withFreshReauthentication(() => revokeSecurityApiKey(row.id))
      setIssuedSecret('')
      await apiKeys.reload()
    })
  }

  function savePolicy(event) {
    event.preventDefault()
    perform('Security policy updated.', async () => {
      await withFreshReauthentication(() => updateIdentityPolicy(policyDraft))
      await policy.reload()
      await posture.reload()
    })
  }

  function createKey(event) {
    event.preventDefault()
    const requestedScopes = keyScopes.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean)
    perform('API key created. Copy the secret below; it will not be shown again.', async () => {
      const issued = await withFreshReauthentication(() => createSecurityApiKey({
        name: keyName.trim(),
        scopes: requestedScopes,
        environment_id: keyEnvironment.trim() || undefined,
      }))
      setIssuedSecret(issued?.secret || '')
      setKeyName('')
      await apiKeys.reload()
      await posture.reload()
    })
  }

  return (
    <>
      <div className="page__header">
        <div>
          <h1>Security & sessions</h1>
          <p className="page__description">
            Live identity policy, your recent sessions, API-key lifecycle, and
            the roles and controls reported by the backend. No certification
            or compliance status is inferred here.
          </p>
        </div>
      </div>

      {notice && <Alert tone="success">{notice}</Alert>}
      {actionError && <Alert tone="error">{actionError}</Alert>}

      {identityRead && (
        <AsyncSection
          loading={posture.loading || policy.loading}
          error={posture.error || policy.error}
          onRetry={() => { posture.reload(); policy.reload() }}
          resource="the tenant security posture"
        >
          {posture.data && (
            <>
              <div className="grid grid--stats">
                <StatCard
                  label="Active sessions"
                  value={formatNumber(posture.data.inventory?.active_sessions ?? 0)}
                  hint="Tenant-wide live inventory"
                />
                <StatCard
                  label="Active API keys"
                  value={formatNumber(posture.data.inventory?.active_api_keys ?? 0)}
                  hint="Tenant-wide live inventory"
                />
                <StatCard
                  label="Environments"
                  value={formatNumber(posture.data.inventory?.environments ?? 0)}
                  hint="Configured for this tenant"
                />
              </div>
              <section className="card" aria-label="Security controls">
                <div className="card__header"><h2>Reported controls</h2></div>
                <div className="card__body">
                  <dl className="kv">
                    {Object.entries(posture.data.controls ?? {}).map(([key, value]) => (
                      <div key={key} className="kv__row">
                        <dt>{humanise(key)}</dt>
                        <dd>{typeof value === 'boolean' ? (value ? 'Configured' : 'Not configured') : String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="muted" style={{ fontSize: 12 }}>
                    These values describe application configuration and database inventory only.
                    They do not represent an external audit or certification.
                  </p>
                </div>
              </section>
            </>
          )}
        </AsyncSection>
      )}

      <section className="card" aria-label="Your sessions">
        <div className="card__header">
          <div>
            <h2>Your sessions</h2>
            <p className="muted" style={{ margin: '2px 0 0' }}>
              Recent session history is limited to your account; other members’ sessions are not shown here.
            </p>
          </div>
        </div>
        <div className="card__body">
          <AsyncSection
            loading={sessions.loading}
            error={sessions.error}
            onRetry={sessions.reload}
            resource="your sessions"
            isEmpty={Boolean(sessions.data) && sessionRows.length === 0}
            empty={<EmptyState title="No session history" description="A session appears after sign-in." />}
          >
            {sessionRows.length > 0 && (
              <DataTable
                caption="Your recent sessions"
                columns={sessionColumns}
                rows={sessionRows}
                keyOf={(row) => row.id}
              />
            )}
          </AsyncSection>
        </div>
      </section>

      {identityRead && (
        <section className="card" aria-label="Identity policy">
          <div className="card__header">
            <div>
              <h2>Identity policy</h2>
              <p className="muted" style={{ margin: '2px 0 0' }}>
                Effective tenant policy from the existing identity-policy service.
              </p>
            </div>
          </div>
          <div className="card__body">
            <AsyncSection
              loading={policy.loading}
              error={policy.error}
              onRetry={policy.reload}
              resource="identity policy"
            >
              {policy.data && (
                <>
                  <dl className="kv">
                    {POLICY_TOGGLES.map(([key, label]) => (
                      <div key={key} className="kv__row">
                        <dt>{label}</dt>
                        <dd>{policy.data[key] ? 'Enabled' : 'Disabled'}</dd>
                      </div>
                    ))}
                    <div className="kv__row"><dt>Session idle minutes</dt><dd>{policy.data.session_idle_minutes}</dd></div>
                    <div className="kv__row"><dt>Maximum active sessions</dt><dd>{policy.data.session_max_active}</dd></div>
                    <div className="kv__row"><dt>Refresh token days</dt><dd>{policy.data.refresh_token_days}</dd></div>
                  </dl>
                  {identityWrite && (
                    <form onSubmit={savePolicy} className="stack" style={{ marginTop: 24 }}>
                      <h3>Update policy</h3>
                      {POLICY_TOGGLES.map(([key, label]) => (
                        <label key={key} className="row" style={{ gap: 10 }}>
                          <input
                            type="checkbox"
                            checked={Boolean(policyDraft[key])}
                            onChange={(event) => setPolicyDraft((old) => ({ ...old, [key]: event.target.checked }))}
                          />
                          <span>{label}</span>
                        </label>
                      ))}
                      <label className="field">
                        <span>Session idle minutes</span>
                        <input
                          className="input"
                          type="number"
                          min="5"
                          max="43200"
                          value={policyDraft.session_idle_minutes}
                          onChange={(event) => setPolicyDraft((old) => ({ ...old, session_idle_minutes: Number(event.target.value) }))}
                        />
                      </label>
                      <label className="field">
                        <span>Maximum active sessions</span>
                        <input
                          className="input"
                          type="number"
                          min="1"
                          max="200"
                          value={policyDraft.session_max_active}
                          onChange={(event) => setPolicyDraft((old) => ({ ...old, session_max_active: Number(event.target.value) }))}
                        />
                      </label>
                      <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
                        <input
                          className="input"
                          type="password"
                          autoComplete="current-password"
                          placeholder="Password for fresh reauthentication"
                          value={reauthPassword}
                          onChange={(event) => setReauthPassword(event.target.value)}
                        />
                        <input
                          className="input"
                          type="text"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          placeholder="MFA code (optional)"
                          value={reauthCode}
                          onChange={(event) => setReauthCode(event.target.value)}
                        />
                        <button className="btn btn--primary" type="submit" disabled={pending}>
                          {pending ? 'Saving…' : 'Reauthenticate & save'}
                        </button>
                      </div>
                    </form>
                  )}
                </>
              )}
            </AsyncSection>
          </div>
        </section>
      )}

      {apiKeyManage && (
        <section className="card" aria-label="API keys">
          <div className="card__header">
            <div>
              <h2>API keys</h2>
              <p className="muted" style={{ margin: '2px 0 0' }}>
                Secrets are shown only at creation or rotation. A key can be limited to one environment.
              </p>
            </div>
          </div>
          <div className="card__body">
            {issuedSecret && (
              <Alert tone="warning">
                Copy this one-time secret now. It is not retained after this page is closed.
                <div style={{ marginTop: 8, overflowWrap: 'anywhere' }}><code>{issuedSecret}</code></div>
              </Alert>
            )}
            <form onSubmit={createKey} className="stack" style={{ marginBottom: 24 }}>
              <h3>Create a scoped key</h3>
              <label className="field">
                <span>Name</span>
                <input className="input" required minLength="1" maxLength="120" value={keyName} onChange={(event) => setKeyName(event.target.value)} />
              </label>
              <label className="field">
                <span>Permission scopes (comma or space separated)</span>
                <input className="input" value={keyScopes} onChange={(event) => setKeyScopes(event.target.value)} placeholder="call:read lead:read" />
              </label>
              {grantableScopes.length > 0 && (
                <p className="muted" style={{ fontSize: 12, marginTop: -8 }}>
                  Grantable scopes: {grantableScopes.join(', ')}
                </p>
              )}
              <label className="field">
                <span>Environment UUID (optional; empty means tenant-wide)</span>
                <input className="input" value={keyEnvironment} onChange={(event) => setKeyEnvironment(event.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
              </label>
              <button className="btn btn--primary" type="submit" disabled={pending || !keyName.trim()}>
                {pending ? 'Working…' : 'Reauthenticate & create key'}
              </button>
            </form>
            <AsyncSection
              loading={apiKeys.loading || scopes.loading}
              error={apiKeys.error || scopes.error}
              onRetry={() => { apiKeys.reload(); scopes.reload() }}
              resource="API keys"
              isEmpty={Boolean(apiKeys.data) && keyRows.length === 0}
              empty={<EmptyState title="No API keys" description="Create a scoped key when an integration needs one." />}
            >
              {keyRows.length > 0 && (
                <DataTable
                  caption="Tenant API keys"
                  columns={keyColumns}
                  rows={keyRows}
                  keyOf={(row) => row.id}
                />
              )}
            </AsyncSection>
          </div>
        </section>
      )}

      {identityRead && postureRoles.length > 0 && (
        <section className="card" aria-label="Roles and permissions">
          <div className="card__header"><h2>Roles & permissions</h2></div>
          <div className="card__body stack">
            {postureRoles.map((role) => (
              <details key={role.role} className="card">
                <summary className="card__header" style={{ cursor: 'pointer' }}>
                  <strong>{humanise(role.role)}</strong>
                  <span className="muted">{role.level} · {role.permissions?.length ?? 0} permissions</span>
                </summary>
                <div className="card__body">
                  <ul>{(role.permissions ?? []).map((permission) => <li key={permission}><code>{permission}</code></li>)}</ul>
                </div>
              </details>
            ))}
          </div>
        </section>
      )}
    </>
  )
}
