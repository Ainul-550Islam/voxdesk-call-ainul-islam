/**
 * dashboard/src/pages/settings/IdentitySettingsPage.tsx
 * SSO connections, SCIM credentials, API keys, service accounts, verified domains.
 *
 * Data: `api/identity.ts` (app/api/identity_routes.py and the sibling SSO/SCIM/
 * API-key/service-account/domain routers, all mounted under `/api/identity`).
 *
 * Secrets are shown exactly once: `ApiKeyCreated.secret` and
 * `ScimCredentialCreated.token` are returned by the create/rotate call and never
 * by any read endpoint. They render in a `OneTimeSecret` panel that is cleared
 * as soon as the operator dismisses it or switches tab — they are never held in a
 * list, and never re-fetched.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ApiKey,
  ApiKeyCreated,
  Domain,
  DomainCreated,
  ScimCredential,
  ScimCredentialCreated,
  ServiceAccount,
  SsoCertificate,
  SsoConnection,
  SsoConnectionInput,
  addDomain,
  addSsoCertificate,
  challengeDomain,
  createApiKey,
  createScimCredential,
  createSsoConnection,
  createServiceAccount,
  deleteServiceAccount,
  deleteSsoConnection,
  disableServiceAccount,
  enableServiceAccount,
  listApiKeys,
  listDomains,
  listScimCredentials,
  listServiceAccounts,
  listSsoCertificates,
  listSsoConnections,
  removeDomain,
  revokeApiKey,
  revokeScimCredential,
  rotateApiKey,
  rotateScimCredential,
  setSsoConnectionStatus,
  testSsoConnection,
  verifyDomain,
} from '../../api/identity';
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

type Tab = 'sso' | 'scim' | 'keys' | 'service-accounts' | 'domains';

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'sso', label: 'SSO' },
  { key: 'scim', label: 'SCIM' },
  { key: 'keys', label: 'API keys' },
  { key: 'service-accounts', label: 'Service accounts' },
  { key: 'domains', label: 'Domains' },
];

interface OneTimeSecret {
  label: string;
  value: string;
  warning?: string;
}

function OneTimeSecretPanel({ secret, onDismiss }: { secret: OneTimeSecret; onDismiss: () => void }) {
  return (
    <div
      role="status"
      data-testid="one-time-secret"
      style={{ background: '#2a1f0b', border: '1px solid #7a5a12', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 12.5, color: '#f7e6bd' }}
    >
      <strong>{secret.label} — shown once, store it now.</strong>
      <pre style={{ margin: '6px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{secret.value}</pre>
      {secret.warning && <p style={{ margin: '0 0 6px' }}>{secret.warning}</p>}
      <button style={subtleButtonStyle} onClick={onDismiss}>
        I have stored it
      </button>
    </div>
  );
}

export function IdentitySettingsPage() {
  const [tab, setTab] = useState<Tab>('sso');
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [secret, setSecret] = useState<OneTimeSecret | null>(null);

  // SSO
  const [connections, setConnections] = useState<SsoConnection[] | null>(null);
  const [connectionsError, setConnectionsError] = useState<unknown>(null);
  const [certificates, setCertificates] = useState<Record<string, SsoCertificate[]>>({});
  const [ssoDraft, setSsoDraft] = useState<SsoConnectionInput>({ name: '', protocol: 'saml' });
  const [showSsoForm, setShowSsoForm] = useState(false);

  // SCIM
  const [scim, setScim] = useState<ScimCredential[] | null>(null);
  const [scimError, setScimError] = useState<unknown>(null);

  // API keys
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [keysError, setKeysError] = useState<unknown>(null);
  const [keyName, setKeyName] = useState('');
  const [keyScopes, setKeyScopes] = useState('');

  // Service accounts
  const [accounts, setAccounts] = useState<ServiceAccount[] | null>(null);
  const [accountsError, setAccountsError] = useState<unknown>(null);
  const [accountName, setAccountName] = useState('');
  const [accountScopes, setAccountScopes] = useState('');

  // Domains
  const [domains, setDomains] = useState<Domain[] | null>(null);
  const [domainsError, setDomainsError] = useState<unknown>(null);
  const [domainDraft, setDomainDraft] = useState('');
  const [dnsRecord, setDnsRecord] = useState<DomainCreated | null>(null);

  const loadSso = useCallback(async () => {
    setConnectionsError(null);
    try {
      setConnections(await listSsoConnections());
    } catch (e) {
      setConnectionsError(e);
      setConnections([]);
    }
  }, []);

  const loadScim = useCallback(async () => {
    setScimError(null);
    try {
      setScim(await listScimCredentials(true));
    } catch (e) {
      setScimError(e);
      setScim([]);
    }
  }, []);

  const loadKeys = useCallback(async () => {
    setKeysError(null);
    try {
      setKeys(await listApiKeys({ includeRevoked: true, limit: 100 }));
    } catch (e) {
      setKeysError(e);
      setKeys([]);
    }
  }, []);

  const loadAccounts = useCallback(async () => {
    setAccountsError(null);
    try {
      setAccounts(await listServiceAccounts());
    } catch (e) {
      setAccountsError(e);
      setAccounts([]);
    }
  }, []);

  const loadDomains = useCallback(async () => {
    setDomainsError(null);
    try {
      setDomains(await listDomains());
    } catch (e) {
      setDomainsError(e);
      setDomains([]);
    }
  }, []);

  useEffect(() => {
    setSecret(null);
    if (tab === 'sso') void loadSso();
    if (tab === 'scim') void loadScim();
    if (tab === 'keys') void loadKeys();
    if (tab === 'service-accounts') void loadAccounts();
    if (tab === 'domains') void loadDomains();
  }, [tab, loadSso, loadScim, loadKeys, loadAccounts, loadDomains]);

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  /* ------------------------------------------------------------------ SSO --- */

  function createConnection(event: React.FormEvent) {
    event.preventDefault();
    void run('sso:create', async () => {
      await createSsoConnection({ ...ssoDraft, name: ssoDraft.name.trim() });
      setShowSsoForm(false);
      setSsoDraft({ name: '', protocol: 'saml' });
      await loadSso();
    });
  }

  function loadCertificates(connectionId: string) {
    void run(`certs:${connectionId}`, async () => {
      const rows = await listSsoCertificates(connectionId);
      setCertificates((prev) => ({ ...prev, [connectionId]: rows }));
    });
  }

  /* ----------------------------------------------------------------- SCIM --- */

  function createScim() {
    void run('scim:create', async () => {
      const created: ScimCredentialCreated = await createScimCredential({ label: 'SCIM provisioning' });
      setSecret({ label: 'SCIM bearer token', value: created.token, warning: created.warning });
      await loadScim();
    });
  }

  /* -------------------------------------------------------------- API keys --- */

  function createKey(event: React.FormEvent) {
    event.preventDefault();
    void run('key:create', async () => {
      const created: ApiKeyCreated = await createApiKey({
        name: keyName.trim(),
        scopes: keyScopes
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
      });
      setSecret({ label: `API key “${created.name}”`, value: created.secret, warning: created.warning });
      setKeyName('');
      setKeyScopes('');
      await loadKeys();
    });
  }

  /* -------------------------------------------------------- service accounts --- */

  function createAccount(event: React.FormEvent) {
    event.preventDefault();
    void run('account:create', async () => {
      await createServiceAccount({
        name: accountName.trim(),
        scopes: accountScopes
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
      });
      setAccountName('');
      setAccountScopes('');
      await loadAccounts();
    });
  }

  /* --------------------------------------------------------------- domains --- */

  function submitDomain(event: React.FormEvent) {
    event.preventDefault();
    void run('domain:add', async () => {
      const created = await addDomain(domainDraft.trim());
      setDnsRecord(created);
      setDomainDraft('');
      await loadDomains();
    });
  }

  return (
    <main data-testid="identity-settings-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Identity settings</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Single sign-on, provisioning, machine credentials and domain ownership for this workspace.
      </p>

      <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        {TABS.map((entry) => (
          <button
            key={entry.key}
            data-testid={`identity-tab-${entry.key}`}
            onClick={() => setTab(entry.key)}
            style={{
              ...subtleButtonStyle,
              background: tab === entry.key ? '#1b3a6b' : 'transparent',
              color: tab === entry.key ? '#fff' : '#a9bccf',
            }}
          >
            {entry.label}
          </button>
        ))}
      </div>

      <ErrorState error={actionError} />
      {secret && <OneTimeSecretPanel secret={secret} onDismiss={() => setSecret(null)} />}

      {tab === 'sso' && (
        <>
          <ConsoleCard
            title={`SSO connections${connections ? ` (${connections.length})` : ''}`}
            testId="sso-connections"
            actions={
              <button style={buttonStyle} onClick={() => setShowSsoForm((value) => !value)} data-testid="sso-new">
                {showSsoForm ? 'Cancel' : 'New connection'}
              </button>
            }
          >
            <ErrorState error={connectionsError} onRetry={() => void loadSso()} />
            {showSsoForm && (
              <form onSubmit={createConnection} style={{ marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
                  <Field label="Display name">
                    <input style={inputStyle} data-testid="sso-name" value={ssoDraft.name} onChange={(e) => setSsoDraft({ ...ssoDraft, name: e.target.value })} required />
                  </Field>
                  <Field label="Protocol">
                    <select style={inputStyle} value={ssoDraft.protocol} onChange={(e) => setSsoDraft({ ...ssoDraft, protocol: e.target.value })}>
                      <option value="saml">SAML 2.0</option>
                      <option value="oidc">OpenID Connect</option>
                    </select>
                  </Field>
                  {ssoDraft.protocol === 'oidc' ? (
                    <>
                      <Field label="Issuer">
                        <input style={inputStyle} value={ssoDraft.issuer ?? ''} onChange={(e) => setSsoDraft({ ...ssoDraft, issuer: e.target.value })} />
                      </Field>
                      <Field label="Client id">
                        <input style={inputStyle} value={ssoDraft.client_id ?? ''} onChange={(e) => setSsoDraft({ ...ssoDraft, client_id: e.target.value })} />
                      </Field>
                      <Field label="Client secret (write-only)">
                        <input style={inputStyle} type="password" value={ssoDraft.client_secret ?? ''} onChange={(e) => setSsoDraft({ ...ssoDraft, client_secret: e.target.value })} />
                      </Field>
                    </>
                  ) : (
                    <>
                      <Field label="IdP SSO URL">
                        <input style={inputStyle} value={ssoDraft.idp_sso_url ?? ''} onChange={(e) => setSsoDraft({ ...ssoDraft, idp_sso_url: e.target.value })} />
                      </Field>
                      <Field label="IdP entity id">
                        <input style={inputStyle} value={ssoDraft.idp_entity_id ?? ''} onChange={(e) => setSsoDraft({ ...ssoDraft, idp_entity_id: e.target.value })} />
                      </Field>
                      <Field label="IdP metadata URL">
                        <input style={inputStyle} value={ssoDraft.metadata_url ?? ''} onChange={(e) => setSsoDraft({ ...ssoDraft, metadata_url: e.target.value })} />
                      </Field>
                    </>
                  )}
                </div>
                <button type="submit" style={buttonStyle} disabled={busy !== null} data-testid="sso-create">
                  Create connection
                </button>
              </form>
            )}

            {connections === null ? (
              <LoadingState label="Loading SSO connections…" />
            ) : connections.length === 0 ? (
              <EmptyState title="No SSO connections" detail="Create one to let members sign in through an identity provider." />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Name</th>
                    <th style={thStyle}>Protocol</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Certificates</th>
                    <th style={thStyle}>Last login</th>
                    <th style={thStyle} />
                  </tr>
                </thead>
                <tbody>
                  {connections.map((connection) => (
                    <React.Fragment key={connection.id}>
                      <tr data-testid={`sso-row-${connection.id}`}>
                        <td style={tdStyle}>
                          {connection.name}
                          <div style={{ fontSize: 10.5, color: '#5d7186' }}>{connection.slug}</div>
                        </td>
                        <td style={tdStyle}>{connection.protocol}</td>
                        <td style={tdStyle}>
                          <Badge tone={connection.status === 'active' ? 'ok' : 'warn'}>{connection.status}</Badge>
                        </td>
                        <td style={tdStyle}>{connection.active_certificates ?? 0}</td>
                        <td style={tdStyle}>{formatDate(connection.last_login_at)}</td>
                        <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                          <button
                            style={subtleButtonStyle}
                            disabled={busy !== null}
                            onClick={() =>
                              void run(`sso:status:${connection.id}`, async () => {
                                await setSsoConnectionStatus(connection.id, connection.status === 'active' ? 'inactive' : 'active');
                                await loadSso();
                              })
                            }
                          >
                            {connection.status === 'active' ? 'Deactivate' : 'Activate'}
                          </button>{' '}
                          <button
                            style={subtleButtonStyle}
                            disabled={busy !== null}
                            onClick={() => loadCertificates(connection.id)}
                          >
                            Certificates
                          </button>{' '}
                          <button
                            style={subtleButtonStyle}
                            disabled={busy !== null}
                            onClick={() =>
                              void run(`sso:test:${connection.id}`, async () => {
                                const result = await testSsoConnection(connection.id);
                                setActionError(
                                  result.ok === false
                                    ? new Error(`SSO test failed: ${result.detail ?? result.status ?? 'provider returned a failure'}`)
                                    : null,
                                );
                              })
                            }
                          >
                            Test
                          </button>{' '}
                          <button
                            style={subtleButtonStyle}
                            disabled={busy !== null}
                            onClick={() =>
                              void run(`sso:delete:${connection.id}`, async () => {
                                await deleteSsoConnection(connection.id);
                                await loadSso();
                              })
                            }
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                      {certificates[connection.id] && (
                        <tr>
                          <td colSpan={6} style={{ ...tdStyle, background: '#0a0f15' }}>
                            <strong style={{ fontSize: 12 }}>Signing certificates</strong>
                            {certificates[connection.id].length === 0 ? (
                              <p style={{ fontSize: 12, color: '#8fa3b8', margin: '6px 0' }}>No certificates registered.</p>
                            ) : (
                              <ul style={{ margin: '6px 0 6px 16px', padding: 0, fontSize: 11.5 }}>
                                {certificates[connection.id].map((certificate) => (
                                  <li key={certificate.id}>
                                    {certificate.fingerprint ?? certificate.id} · active {String(certificate.is_active)} · expires{' '}
                                    {formatDate(certificate.not_after)}
                                  </li>
                                ))}
                              </ul>
                            )}
                            <form
                              onSubmit={(event) => {
                                event.preventDefault();
                                const input = (event.currentTarget.elements.namedItem('pem') as HTMLInputElement | null)?.value ?? '';
                                if (!input.trim()) return;
                                void run(`cert:add:${connection.id}`, async () => {
                                  await addSsoCertificate(connection.id, input);
                                  loadCertificates(connection.id);
                                });
                              }}
                            >
                              <textarea
                                name="pem"
                                placeholder="-----BEGIN CERTIFICATE-----"
                                style={{ ...inputStyle, minHeight: 60, fontFamily: 'monospace', fontSize: 11 }}
                              />
                              <button type="submit" style={subtleButtonStyle} disabled={busy !== null}>
                                Add certificate
                              </button>
                            </form>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>
        </>
      )}

      {tab === 'scim' && (
        <ConsoleCard
          title="SCIM credentials"
          testId="scim-credentials"
          description="Bearer tokens your identity provider uses to provision users. Revoked rows are shown so the history stays auditable."
          actions={
            <button style={buttonStyle} onClick={createScim} disabled={busy !== null} data-testid="scim-create">
              {busy === 'scim:create' ? 'Creating…' : 'New token'}
            </button>
          }
        >
          <ErrorState error={scimError} onRetry={() => void loadScim()} />
          {scim === null ? (
            <LoadingState label="Loading SCIM credentials…" />
          ) : scim.length === 0 ? (
            <EmptyState title="No SCIM credentials" detail="Create a token in your IdP to start provisioning." />
          ) : (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Label</th>
                  <th style={thStyle}>Prefix</th>
                  <th style={thStyle}>Scopes</th>
                  <th style={thStyle}>Expires</th>
                  <th style={thStyle}>Last used</th>
                  <th style={thStyle} />
                </tr>
              </thead>
              <tbody>
                {scim.map((credential) => (
                  <tr key={credential.id} data-testid={`scim-row-${credential.id}`}>
                    <td style={tdStyle}>
                      {credential.label || '—'}
                      {credential.revoked_at && (
                        <div>
                          <Badge tone="bad">revoked {formatDate(credential.revoked_at)}</Badge>
                        </div>
                      )}
                    </td>
                    <td style={tdStyle}>{credential.prefix}…</td>
                    <td style={tdStyle}>{credential.scopes.join(', ') || '—'}</td>
                    <td style={tdStyle}>{formatDate(credential.expires_at)}</td>
                    <td style={tdStyle}>{formatDate(credential.last_used_at)}</td>
                    <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                      <button
                        style={subtleButtonStyle}
                        disabled={busy !== null || Boolean(credential.revoked_at)}
                        onClick={() =>
                          void run(`scim:rotate:${credential.id}`, async () => {
                            const rotated = await rotateScimCredential(credential.id);
                            setSecret({ label: 'Rotated SCIM token', value: rotated.token, warning: rotated.warning });
                            await loadScim();
                          })
                        }
                      >
                        Rotate
                      </button>{' '}
                      <button
                        style={subtleButtonStyle}
                        disabled={busy !== null || Boolean(credential.revoked_at)}
                        onClick={() =>
                          void run(`scim:revoke:${credential.id}`, async () => {
                            await revokeScimCredential(credential.id);
                            await loadScim();
                          })
                        }
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ConsoleCard>
      )}

      {tab === 'keys' && (
        <>
          <ConsoleCard title="Create API key" testId="api-key-create">
            <form onSubmit={createKey}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
                <Field label="Name">
                  <input style={inputStyle} data-testid="api-key-name" value={keyName} onChange={(e) => setKeyName(e.target.value)} required />
                </Field>
                <Field label="Scopes (comma separated; empty inherits your role)">
                  <input style={inputStyle} value={keyScopes} onChange={(e) => setKeyScopes(e.target.value)} placeholder="calls:read, leads:read" />
                </Field>
              </div>
              <button type="submit" style={buttonStyle} disabled={busy !== null} data-testid="api-key-submit">
                Create key
              </button>
            </form>
          </ConsoleCard>

          <ConsoleCard title="API keys" testId="api-keys">
            <ErrorState error={keysError} onRetry={() => void loadKeys()} />
            {keys === null ? (
              <LoadingState label="Loading API keys…" />
            ) : keys.length === 0 ? (
              <EmptyState title="No API keys" detail="Keys authenticate server-to-server calls; they are separate from user sessions." />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Name</th>
                    <th style={thStyle}>Prefix</th>
                    <th style={thStyle}>Scopes</th>
                    <th style={thStyle}>Created</th>
                    <th style={thStyle}>Last used</th>
                    <th style={thStyle} />
                  </tr>
                </thead>
                <tbody>
                  {keys.map((key) => (
                    <tr key={key.id} data-testid={`api-key-row-${key.id}`}>
                      <td style={tdStyle}>
                        {key.name}
                        {key.revoked_at && (
                          <div>
                            <Badge tone="bad">revoked</Badge>
                          </div>
                        )}
                      </td>
                      <td style={tdStyle}>{key.prefix}…</td>
                      <td style={tdStyle}>{key.scopes.join(', ') || 'role default'}</td>
                      <td style={tdStyle}>{formatDate(key.created_at)}</td>
                      <td style={tdStyle}>{formatDate(key.last_used_at)}</td>
                      <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null || Boolean(key.revoked_at)}
                          onClick={() =>
                            void run(`key:rotate:${key.id}`, async () => {
                              const rotated = await rotateApiKey(key.id);
                              setSecret({ label: `Rotated key “${rotated.name}”`, value: rotated.secret, warning: rotated.warning });
                              await loadKeys();
                            })
                          }
                        >
                          Rotate
                        </button>{' '}
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null || Boolean(key.revoked_at)}
                          onClick={() =>
                            void run(`key:revoke:${key.id}`, async () => {
                              await revokeApiKey(key.id);
                              await loadKeys();
                            })
                          }
                        >
                          Revoke
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>
        </>
      )}

      {tab === 'service-accounts' && (
        <>
          <ConsoleCard title="Create service account" testId="service-account-create">
            <form onSubmit={createAccount}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
                <Field label="Name">
                  <input style={inputStyle} data-testid="account-name" value={accountName} onChange={(e) => setAccountName(e.target.value)} required />
                </Field>
                <Field label="Scopes (comma separated)">
                  <input style={inputStyle} value={accountScopes} onChange={(e) => setAccountScopes(e.target.value)} />
                </Field>
              </div>
              <button type="submit" style={buttonStyle} disabled={busy !== null} data-testid="account-submit">
                Create
              </button>
            </form>
          </ConsoleCard>

          <ConsoleCard title="Service accounts" testId="service-accounts">
            <ErrorState error={accountsError} onRetry={() => void loadAccounts()} />
            {accounts === null ? (
              <LoadingState label="Loading service accounts…" />
            ) : accounts.length === 0 ? (
              <EmptyState title="No service accounts" detail="A service account holds credentials for automation that must not belong to a person." />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Name</th>
                    <th style={thStyle}>Scopes</th>
                    <th style={thStyle}>State</th>
                    <th style={thStyle}>Credentials</th>
                    <th style={thStyle} />
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((account) => (
                    <tr key={account.id} data-testid={`account-row-${account.id}`}>
                      <td style={tdStyle}>
                        {account.name}
                        {account.description && <div style={{ fontSize: 10.5, color: '#5d7186' }}>{account.description}</div>}
                      </td>
                      <td style={tdStyle}>{account.scope_summary || account.scopes.join(', ') || '—'}</td>
                      <td style={tdStyle}>
                        <Badge tone={account.enabled ? 'ok' : 'bad'}>{account.enabled ? 'enabled' : 'disabled'}</Badge>
                        {account.emergency_disabled && <div style={{ fontSize: 10.5, color: '#f2a1a1' }}>emergency disabled</div>}
                      </td>
                      <td style={tdStyle}>{account.credential_count ?? 0}</td>
                      <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`account:toggle:${account.id}`, async () => {
                              if (account.enabled) await disableServiceAccount(account.id);
                              else await enableServiceAccount(account.id);
                              await loadAccounts();
                            })
                          }
                        >
                          {account.enabled ? 'Disable' : 'Enable'}
                        </button>{' '}
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`account:delete:${account.id}`, async () => {
                              await deleteServiceAccount(account.id);
                              await loadAccounts();
                            })
                          }
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>
        </>
      )}

      {tab === 'domains' && (
        <>
          <ConsoleCard title="Add domain" testId="domain-add">
            <form onSubmit={submitDomain} style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 240px' }}>
                <Field label="Domain you control">
                  <input style={inputStyle} data-testid="domain-input" value={domainDraft} onChange={(e) => setDomainDraft(e.target.value)} placeholder="example.com" required />
                </Field>
              </div>
              <button type="submit" style={buttonStyle} disabled={busy !== null} data-testid="domain-submit">
                Add domain
              </button>
            </form>
            {dnsRecord && (
              <div data-testid="domain-dns-record" style={{ marginTop: 12, background: '#101a2b', border: '1px solid #1d2836', borderRadius: 8, padding: 10, fontSize: 12 }}>
                <strong>DNS record to publish</strong>
                <pre style={{ margin: '6px 0 0', whiteSpace: 'pre-wrap' }}>
                  {dnsRecord.verification_record_name} TXT {dnsRecord.verification_record_value}
                </pre>
              </div>
            )}
          </ConsoleCard>

          <ConsoleCard title="Verified domains" testId="domains">
            <ErrorState error={domainsError} onRetry={() => void loadDomains()} />
            {domains === null ? (
              <LoadingState label="Loading domains…" />
            ) : domains.length === 0 ? (
              <EmptyState title="No domains" detail="A verified domain lets you require SSO for everyone with that email suffix." />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Domain</th>
                    <th style={thStyle}>Verified</th>
                    <th style={thStyle}>Enforcement</th>
                    <th style={thStyle}>Password login</th>
                    <th style={thStyle} />
                  </tr>
                </thead>
                <tbody>
                  {domains.map((domain) => (
                    <tr key={domain.id} data-testid={`domain-row-${domain.id}`}>
                      <td style={tdStyle}>{domain.domain}</td>
                      <td style={tdStyle}>
                        <Badge tone={domain.verified ? 'ok' : 'warn'}>{domain.verified ? 'verified' : domain.verification_status ?? 'pending'}</Badge>
                      </td>
                      <td style={tdStyle}>{domain.enforcement ?? '—'}</td>
                      <td style={tdStyle}>{domain.block_password_login ? 'blocked' : 'allowed'}</td>
                      <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                        {!domain.verified && (
                          <>
                            <button
                              style={subtleButtonStyle}
                              disabled={busy !== null}
                              onClick={() =>
                                void run(`domain:challenge:${domain.id}`, async () => {
                                  const created = await challengeDomain(domain.id);
                                  setDnsRecord(created);
                                })
                              }
                            >
                              Re-issue record
                            </button>{' '}
                            <button
                              style={subtleButtonStyle}
                              disabled={busy !== null}
                              onClick={() =>
                                void run(`domain:verify:${domain.id}`, async () => {
                                  const result = await verifyDomain(domain.id);
                                  setActionError(result.verified ? null : new Error(`Not verified yet: ${result.detail}`));
                                  await loadDomains();
                                })
                              }
                            >
                              Verify now
                            </button>{' '}
                          </>
                        )}
                        <button
                          style={subtleButtonStyle}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`domain:remove:${domain.id}`, async () => {
                              await removeDomain(domain.id);
                              await loadDomains();
                            })
                          }
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ConsoleCard>
        </>
      )}
    </main>
  );
}

export default IdentitySettingsPage;
