/**
 * dashboard/src/pages/settings/WorkspaceSettingsPage.tsx
 * Workspace profile, identity policy, deployment posture and live sessions.
 *
 * Data: `api/settings.ts` →
 *   GET   /api/tenants/{tenant_id}/settings            (allowlist view only)
 *   PATCH /api/tenants/{tenant_id}/profile             ({name} only)
 *   GET   /api/tenants/{tenant_id}/security/posture    (server-resolved hierarchy)
 *   GET   /api/identity/policy   PATCH /api/identity/policy
 *   GET   /api/identity/status   GET /api/mfa/status
 *   GET   /api/sessions          DELETE /api/sessions/{id}
 *   POST  /api/sessions/revoke-others
 *
 * Two constraints from the backend shape this page:
 *   • `tenant_settings_view` returns a fixed allowlist. Routing/secret fields are
 *     never returned, so this page renders only what arrives and marks everything
 *     except `name` as read-only rather than pretending to edit it.
 *   • `PolicyIn` is `extra="forbid"`. The policy form therefore diffs against the
 *     loaded document and PATCHes only the keys the operator actually changed.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  IdentityPolicy,
  IdentityPolicyPatch,
  IdentityStatus,
  MfaStatus,
  SecurityPosture,
  SessionList,
  WorkspaceSettings,
  getIdentityPolicy,
  getIdentityStatus,
  getMfaStatus,
  getSecurityPosture,
  getWorkspaceSettings,
  listSessions,
  revokeOtherSessions,
  revokeSession,
  updateIdentityPolicy,
  updateWorkspaceProfile,
} from '../../api/settings';
import { getAuthSession } from '../../lib/session';
import {
  Badge,
  ConsoleCard,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  buttonStyle,
  formatDate,
  inputStyle,
  subtleButtonStyle,
  tableStyle,
  tdStyle,
  thStyle,
} from '../../components/layout/ConsoleStates';

const POLICY_TOGGLES: Array<{ key: keyof IdentityPolicy; label: string; help: string }> = [
  { key: 'mfa_required', label: 'Require MFA for everyone', help: 'Every member must enrol a second factor.' },
  { key: 'mfa_required_for_admins', label: 'Require MFA for admins', help: 'Owner/admin roles only.' },
  { key: 'privileged_reauth_required', label: 'Fresh reauth for privileged actions', help: 'Confirm the password again before destructive changes.' },
  { key: 'sso_required', label: 'Require SSO', help: 'Password login is refused for covered domains.' },
  { key: 'password_login_allowed', label: 'Allow password login', help: 'Off means SSO-only sign-in.' },
  { key: 'api_keys_allowed', label: 'Allow API keys', help: 'Machine credentials may be created.' },
  { key: 'service_accounts_allowed', label: 'Allow service accounts', help: 'Non-human owners for automation.' },
  { key: 'scim_enabled', label: 'SCIM provisioning', help: 'The IdP may create and update members.' },
  { key: 'jit_provisioning_allowed', label: 'Just-in-time provisioning', help: 'Create a member on first SSO login.' },
];

const POLICY_NUMBERS: Array<{ key: keyof IdentityPolicy; label: string; min: number; max: number }> = [
  { key: 'privileged_reauth_minutes', label: 'Privileged reauth window (minutes)', min: 1, max: 1440 },
  { key: 'session_idle_minutes', label: 'Session idle timeout (minutes)', min: 1, max: 10080 },
  { key: 'session_max_active', label: 'Max concurrent sessions', min: 1, max: 100 },
  { key: 'refresh_token_days', label: 'Refresh token lifetime (days)', min: 1, max: 365 },
];

export function WorkspaceSettingsPage() {
  const [settings, setSettings] = useState<WorkspaceSettings | null>(null);
  const [posture, setPosture] = useState<SecurityPosture | null>(null);
  const [policy, setPolicy] = useState<IdentityPolicy | null>(null);
  const [policyDraft, setPolicyDraft] = useState<Partial<IdentityPolicy>>({});
  const [identity, setIdentity] = useState<IdentityStatus | null>(null);
  const [mfa, setMfa] = useState<MfaStatus | null>(null);
  const [sessions, setSessions] = useState<SessionList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState('');
  const [includeRevoked, setIncludeRevoked] = useState(false);

  const session = getAuthSession();
  const tenantId = (session?.user?.tenant_id as string | undefined) ?? (session?.tenant?.id as string | undefined) ?? null;

  const loadSessions = useCallback(async (withRevoked: boolean) => {
    try {
      setSessions(await listSessions(withRevoked));
    } catch (e) {
      setActionError(e);
      setSessions({ sessions: [], limits: {} });
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Workspace-scoped reads need the tenant; identity reads are per-person and
      // do not. Both groups are optional so one 403 cannot blank the page.
      const [settingsResult, policyResult, identityResult, mfaResult, postureResult] = await Promise.all([
        tenantId ? getWorkspaceSettings(tenantId) : Promise.resolve(null),
        getIdentityPolicy().catch(() => null),
        getIdentityStatus().catch(() => null),
        getMfaStatus().catch(() => null),
        tenantId ? getSecurityPosture(tenantId).catch(() => null) : Promise.resolve(null),
      ]);
      setSettings(settingsResult);
      if (settingsResult) setNameDraft(settingsResult.name);
      setPolicy(policyResult);
      setPolicyDraft({});
      setIdentity(identityResult);
      setMfa(mfaResult);
      setPosture(postureResult);
      if (!settingsResult && !policyResult && !identityResult) {
        setError(new Error('No settings endpoint was readable for this account.'));
      }
      await loadSessions(includeRevoked);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [tenantId, includeRevoked, loadSessions]);

  useEffect(() => {
    void load();
    // `includeRevoked` is handled by the dedicated toggle below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setActionError(null);
    setNotice(null);
    try {
      await action();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  function saveName(event: React.FormEvent) {
    event.preventDefault();
    if (!tenantId) return;
    void run('profile', async () => {
      const updated = await updateWorkspaceProfile(tenantId, nameDraft.trim());
      setSettings((prev) => (prev ? { ...prev, name: updated.name } : prev));
      setNotice(`Workspace renamed to “${updated.name}”.`);
    });
  }

  function savePolicy() {
    // Only changed keys: PolicyIn rejects unknown or extraneous ones.
    const patch: IdentityPolicyPatch = {};
    for (const [key, value] of Object.entries(policyDraft) as Array<[keyof IdentityPolicy, unknown]>) {
      if (value !== undefined) (patch as Record<string, unknown>)[key] = value;
    }
    if (Object.keys(patch).length === 0) {
      setActionError(new Error('No policy changes to save.'));
      return;
    }
    void run('policy', async () => {
      const updated = await updateIdentityPolicy(patch);
      setPolicy(updated);
      setPolicyDraft({});
      setNotice('Identity policy updated.');
    });
  }

  const effectivePolicyValue = <K extends keyof IdentityPolicy>(key: K): IdentityPolicy[K] | undefined => {
    const draft = policyDraft[key];
    if (draft !== undefined) return draft as IdentityPolicy[K];
    return policy ? policy[key] : undefined;
  };

  const dirtyCount = Object.values(policyDraft).filter((value) => value !== undefined).length;

  if (loading) return <LoadingState label="Loading workspace settings…" />;

  return (
    <main data-testid="workspace-settings-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Workspace settings</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Profile, identity policy and the sessions signed in to your account.
      </p>

      <ErrorState error={error} onRetry={() => void load()} />
      <ErrorState error={actionError} />
      {notice && (
        <div role="status" data-testid="workspace-notice" style={{ background: '#0f2418', border: '1px solid #1d4d31', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 13, color: '#c9f3da' }}>
          {notice}
        </div>
      )}

      {!tenantId && (
        <EmptyState
          title="No tenant bound to this session"
          detail="Workspace-scoped settings need a tenant id in the session; the identity controls below still apply to your own account."
        />
      )}

      {settings && tenantId && (
        <ConsoleCard title="Profile" testId="workspace-profile" description="PATCH /api/tenants/{id}/profile accepts the display name only.">
          <form onSubmit={saveName} style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 14 }}>
            <div style={{ flex: '1 1 260px' }}>
              <Field label="Workspace name">
                <input style={inputStyle} data-testid="workspace-name" value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} required />
              </Field>
            </div>
            <button type="submit" style={buttonStyle} disabled={busy !== null || nameDraft.trim() === settings.name} data-testid="workspace-name-save">
              {busy === 'profile' ? 'Saving…' : 'Save name'}
            </button>
          </form>

          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Setting</th>
                <th style={thStyle}>Value</th>
                <th style={thStyle}>Editable here</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ['industry', settings.industry ?? '—'],
                  ['timezone', settings.timezone ?? '—'],
                  ['language', settings.language ?? '—'],
                  ['plan', settings.plan ?? '—'],
                  ['lifecycle_status', settings.lifecycle_status],
                  ['sms_enabled', String(settings.sms_enabled)],
                  ['whatsapp_enabled', String(settings.whatsapp_enabled)],
                  ['outbound_enabled', String(settings.outbound_enabled)],
                  ['record_calls', String(settings.record_calls)],
                  ['ivr_enabled', String(settings.ivr_enabled)],
                  ['humanize', String(settings.humanize)],
                ] as Array<[string, string]>
              ).map(([key, value]) => (
                <tr key={key}>
                  <td style={tdStyle}>{key}</td>
                  <td style={tdStyle}>{value}</td>
                  <td style={{ ...tdStyle, color: '#5d7186' }}>read-only</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: 11.5, color: '#8fa3b8', margin: '10px 0 0' }}>
            These values arrive from the tenant settings allowlist. Fields outside it — routing and provider
            secrets — are never returned by the API and therefore cannot be shown or edited from the console.
          </p>
        </ConsoleCard>
      )}

      {policy && (
        <ConsoleCard
          title="Identity policy"
          testId="workspace-policy"
          description={policy.is_default ? 'No custom policy row yet — this is the deployment default.' : `Custom policy ${policy.identity_policy_id}`}
          actions={
            <button style={buttonStyle} onClick={savePolicy} disabled={busy !== null || dirtyCount === 0} data-testid="policy-save">
              {busy === 'policy' ? 'Saving…' : dirtyCount === 0 ? 'No changes' : `Save ${dirtyCount} change${dirtyCount === 1 ? '' : 's'}`}
            </button>
          }
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 12 }}>
            {POLICY_TOGGLES.map((toggle) => {
              const value = Boolean(effectivePolicyValue(toggle.key));
              const changed = policyDraft[toggle.key] !== undefined;
              return (
                <label
                  key={String(toggle.key)}
                  data-testid={`policy-toggle-${String(toggle.key)}`}
                  style={{
                    display: 'flex',
                    gap: 8,
                    alignItems: 'flex-start',
                    border: `1px solid ${changed ? '#2f6fed' : '#1d2836'}`,
                    borderRadius: 8,
                    padding: 10,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={value}
                    onChange={(e) => setPolicyDraft({ ...policyDraft, [toggle.key]: e.target.checked })}
                  />
                  <span>
                    <span style={{ fontSize: 12.5 }}>{toggle.label}</span>
                    <span style={{ display: 'block', fontSize: 10.5, color: '#8fa3b8' }}>{toggle.help}</span>
                  </span>
                </label>
              );
            })}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12, marginTop: 14 }}>
            {POLICY_NUMBERS.map((field) => (
              <Field key={String(field.key)} label={`${field.label} (${field.min}–${field.max})`}>
                <input
                  type="number"
                  min={field.min}
                  max={field.max}
                  style={inputStyle}
                  value={Number(effectivePolicyValue(field.key) ?? field.min)}
                  onChange={(e) => setPolicyDraft({ ...policyDraft, [field.key]: Number(e.target.value) })}
                />
              </Field>
            ))}
            <Field label="Allowed email domains (comma separated)">
              <input
                style={inputStyle}
                value={(effectivePolicyValue('allowed_email_domains') as string[] | undefined)?.join(', ') ?? ''}
                onChange={(e) =>
                  setPolicyDraft({
                    ...policyDraft,
                    allowed_email_domains: e.target.value
                      .split(',')
                      .map((value) => value.trim())
                      .filter(Boolean),
                  })
                }
              />
            </Field>
          </div>
        </ConsoleCard>
      )}

      <ConsoleCard title="Deployment posture" testId="workspace-posture">
        {!posture && !mfa && !identity ? (
          <EmptyState title="Posture unavailable" detail="Neither the hierarchy nor the identity status endpoint was readable for this account." />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 16 }}>
            {posture && (
              <div>
                <h3 style={{ fontSize: 12.5, color: '#8fa3b8', margin: '0 0 8px' }}>Server-resolved hierarchy</h3>
                <table style={tableStyle}>
                  <tbody>
                    {Object.entries(posture).map(([key, value]) => (
                      <tr key={key}>
                        <td style={{ ...tdStyle, width: 180 }}>{key}</td>
                        <td style={tdStyle}>{value === null ? '—' : String(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div>
              <h3 style={{ fontSize: 12.5, color: '#8fa3b8', margin: '0 0 8px' }}>Your second factor</h3>
              {mfa ? (
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12.5 }}>
                  <li>
                    Enrolled: <Badge tone={mfa.enrolled ? 'ok' : 'bad'}>{mfa.enrolled ? 'yes' : 'no'}</Badge>
                  </li>
                  <li>Required by policy: {mfa.required_by_policy ? 'yes' : 'no'}</li>
                  <li>
                    Recovery codes left: {mfa.recovery_codes_remaining}/{mfa.recovery_codes_total}
                    {mfa.recovery_codes_low && <span style={{ color: '#f0c674' }}> — running low</span>}
                  </li>
                  <li>Last used: {formatDate(mfa.last_used_at)}</li>
                  <li>
                    Deployment TOTP: {mfa.digits} digits / {mfa.period_seconds}s · {mfa.enabled_on_deployment ? 'enabled' : 'disabled'}
                  </li>
                </ul>
              ) : (
                <p style={{ fontSize: 12, color: '#8fa3b8', margin: 0 }}>MFA status was not readable.</p>
              )}
              {identity && (
                <>
                  <h3 style={{ fontSize: 12.5, color: '#8fa3b8', margin: '12px 0 8px' }}>Feature switches</h3>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {Object.entries(identity.features).map(([feature, enabled]) => (
                      <Badge key={feature} tone={enabled ? 'ok' : 'neutral'}>
                        {feature.replace(/_/g, ' ')}
                      </Badge>
                    ))}
                  </div>
                  <p style={{ fontSize: 11.5, color: '#8fa3b8', margin: '8px 0 0' }}>
                    Email verified: {identity.email_verified ? 'yes' : 'no'} · session {identity.session.auth_method ?? '—'}
                  </p>
                </>
              )}
            </div>
          </div>
        )}
      </ConsoleCard>

      <ConsoleCard
        title="Your sessions"
        testId="workspace-sessions"
        description={sessions?.limits?.idle_minutes ? `Idle timeout ${sessions.limits.idle_minutes} min · max ${sessions.limits.max_active} active` : undefined}
        actions={
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <label style={{ fontSize: 12, color: '#8fa3b8' }}>
              <input
                type="checkbox"
                checked={includeRevoked}
                onChange={(e) => {
                  setIncludeRevoked(e.target.checked);
                  void loadSessions(e.target.checked);
                }}
                data-testid="sessions-include-revoked"
              />{' '}
              include revoked
            </label>
            <button
              style={subtleButtonStyle}
              disabled={busy !== null || !sessions || sessions.sessions.length <= 1}
              onClick={() =>
                void run('revoke-others', async () => {
                  const result = await revokeOtherSessions();
                  setNotice(`Signed out ${result.revoked} other session(s).`);
                  await loadSessions(includeRevoked);
                })
              }
              data-testid="revoke-others"
            >
              Sign out other devices
            </button>
          </div>
        }
      >
        {!sessions ? (
          <LoadingState label="Loading sessions…" />
        ) : sessions.sessions.length === 0 ? (
          <EmptyState title="No sessions" detail="This account has no active session rows." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Device</th>
                <th style={thStyle}>IP</th>
                <th style={thStyle}>Auth</th>
                <th style={thStyle}>MFA</th>
                <th style={thStyle}>Last seen</th>
                <th style={thStyle}>Expires</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {sessions.sessions.map((row) => (
                <tr key={row.id} data-testid={`session-row-${row.id}`}>
                  <td style={tdStyle}>
                    {row.device || row.user_agent || 'unknown device'}
                    {row.current && (
                      <>
                        {' '}
                        <Badge tone="ok">this device</Badge>
                      </>
                    )}
                    {row.revoked_at && (
                      <>
                        {' '}
                        <Badge tone="bad">revoked</Badge>
                      </>
                    )}
                  </td>
                  <td style={tdStyle}>{row.ip_address || '—'}</td>
                  <td style={tdStyle}>{row.auth_method}</td>
                  <td style={tdStyle}>{row.mfa_verified ? 'yes' : 'no'}</td>
                  <td style={tdStyle}>{formatDate(row.last_seen_at)}</td>
                  <td style={tdStyle}>{formatDate(row.expires_at)}</td>
                  <td style={tdStyle}>
                    {!row.current && !row.revoked_at && (
                      <button
                        style={subtleButtonStyle}
                        disabled={busy !== null}
                        onClick={() =>
                          void run(`revoke:${row.id}`, async () => {
                            await revokeSession(row.id);
                            setNotice('Session signed out.');
                            await loadSessions(includeRevoked);
                          })
                        }
                      >
                        Sign out
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </main>
  );
}

export default WorkspaceSettingsPage;
