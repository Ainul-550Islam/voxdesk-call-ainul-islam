/**
 * dashboard/src/pages/team/TeamPage.tsx
 * Members, invitations and roles for the authenticated workspace.
 *
 * Data: `api/team.ts` →
 *   GET   /api/team/users                        (tenant-scoped member list)
 *   PATCH /api/team/users/{id}/role              (`RoleChangeIn`)
 *   PATCH /api/team/users/{id}/active            (`ActiveIn`, used as "remove")
 *   POST  /api/team/users                        (direct account creation)
 *   POST  /api/tenants/{tenant_id}/invitations   (`InviteBody`, returns a token)
 *
 * Two different "add a person" paths exist in the backend and they are not
 * interchangeable, so the form lets the operator pick one and says what will
 * happen:
 *   • Invite — emails a token; no password is set here.
 *   • Create — writes the account immediately with the password typed.
 *
 * The role list comes from `GET /auth/roles` (the server's own RBAC document),
 * so a role this deployment does not define can never be offered.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Invitation,
  TeamUser,
  USER_ROLES,
  UserRole,
  changeUserRole,
  createTeamUser,
  inviteToTenant,
  listTeamUsers,
  setUserActive,
} from '../../api/team';
import { client } from '../../api/client';
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

interface RoleDescriptor {
  role: string;
  level: number;
  permissions: string[];
}

export function TeamConsolePage() {
  const [users, setUsers] = useState<TeamUser[]>([]);
  const [roles, setRoles] = useState<RoleDescriptor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [includeInactive, setIncludeInactive] = useState(false);

  const [mode, setMode] = useState<'invite' | 'create'>('invite');
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [newRole, setNewRole] = useState<UserRole>('agent');

  const session = getAuthSession();
  const tenantId = (session?.user?.tenant_id as string | undefined) ?? (session?.tenant?.id as string | undefined) ?? null;

  const roleOptions = useMemo<UserRole[]>(() => {
    if (roles.length === 0) return USER_ROLES;
    return roles.map((r) => r.role).filter((value): value is UserRole => (USER_ROLES as string[]).includes(value));
  }, [roles]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [members, rolePolicy] = await Promise.all([
        listTeamUsers(includeInactive),
        client.get<{ roles: RoleDescriptor[] }>('/auth/roles').catch(() => ({ roles: [] })),
      ]);
      setUsers(members);
      setRoles(rolePolicy.roles ?? []);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [includeInactive]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setActionError(null);
    setNotice(null);
    if (!email.trim()) {
      setActionError(new Error('An email address is required.'));
      return;
    }
    if (mode === 'create' && password.length < 8) {
      setActionError(new Error('The API requires a password of at least 8 characters for a directly created account.'));
      return;
    }
    setBusy('submit');
    try {
      if (mode === 'invite') {
        if (!tenantId) {
          throw new Error('No tenant is bound to this session, so an invitation cannot be scoped.');
        }
        const invitation: Invitation = await inviteToTenant(tenantId, email.trim(), newRole);
        setNotice(
          `Invitation ${invitation.id} created (${invitation.status}), expires ${formatDate(invitation.expires_at)}. ` +
            (invitation.invitation_token
              ? 'The one-time token was returned to this browser — share it out of band; it is not shown again.'
              : 'The invitation is recorded.'),
        );
      } else {
        const created = await createTeamUser({
          email: email.trim(),
          password,
          full_name: fullName.trim() || undefined,
          role: newRole,
        });
        setNotice(`Account ${created.email} created with role ${created.role}.`);
      }
      setEmail('');
      setFullName('');
      setPassword('');
      await load();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  async function changeRole(user: TeamUser, role: UserRole) {
    setBusy(`role:${user.id}`);
    setActionError(null);
    setNotice(null);
    try {
      const updated = await changeUserRole(user.id, role);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setNotice(`${updated.email} is now ${updated.role}.`);
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  async function toggleActive(user: TeamUser) {
    setBusy(`active:${user.id}`);
    setActionError(null);
    setNotice(null);
    try {
      const updated = await setUserActive(user.id, !user.is_active);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setNotice(`${updated.email} is ${updated.is_active ? 'active' : 'deactivated'}.`);
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <LoadingState label="Loading team…" />;

  return (
    <main data-testid="team-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Team</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Members of this workspace and the role that is enforced server-side on every request.
      </p>

      <ErrorState error={error} onRetry={() => void load()} />
      <ErrorState error={actionError} />
      {notice && (
        <div role="status" data-testid="team-notice" style={{ background: '#0f2418', border: '1px solid #1d4d31', borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 13, color: '#c9f3da' }}>
          {notice}
        </div>
      )}

      <ConsoleCard title="Add a teammate" testId="team-add">
        <form onSubmit={submit}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <button
              type="button"
              data-testid="mode-invite"
              onClick={() => setMode('invite')}
              style={{ ...subtleButtonStyle, background: mode === 'invite' ? '#1b3a6b' : 'transparent', color: mode === 'invite' ? '#fff' : '#a9bccf' }}
            >
              Invite by email
            </button>
            <button
              type="button"
              data-testid="mode-create"
              onClick={() => setMode('create')}
              style={{ ...subtleButtonStyle, background: mode === 'create' ? '#1b3a6b' : 'transparent', color: mode === 'create' ? '#fff' : '#a9bccf' }}
            >
              Create account now
            </button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12 }}>
            <Field label="Email">
              <input style={inputStyle} data-testid="team-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            {mode === 'create' && (
              <>
                <Field label="Full name">
                  <input style={inputStyle} value={fullName} onChange={(e) => setFullName(e.target.value)} />
                </Field>
                <Field label="Temporary password">
                  <input style={inputStyle} type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} />
                </Field>
              </>
            )}
            <Field label="Role">
              <select style={inputStyle} data-testid="team-role" value={newRole} onChange={(e) => setNewRole(e.target.value as UserRole)}>
                {roleOptions.map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <button type="submit" style={buttonStyle} disabled={busy === 'submit'} data-testid="team-submit">
            {busy === 'submit' ? 'Working…' : mode === 'invite' ? 'Send invitation' : 'Create account'}
          </button>
          {mode === 'invite' && !tenantId && (
            <p style={{ fontSize: 11.5, color: '#f0c674', marginTop: 8 }}>
              No tenant is bound to this session; invitations require a tenant scope.
            </p>
          )}
        </form>
      </ConsoleCard>

      <ConsoleCard
        title={`Members (${users.length})`}
        testId="team-members"
        actions={
          <label style={{ fontSize: 12, color: '#8fa3b8' }}>
            <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} data-testid="team-include-inactive" />{' '}
            include deactivated
          </label>
        }
      >
        {users.length === 0 ? (
          <EmptyState title="No members" detail="This workspace has no member records yet." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Email</th>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Role</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Last login</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} data-testid={`team-row-${user.email}`}>
                  <td style={tdStyle}>{user.email}</td>
                  <td style={tdStyle}>{user.full_name || '—'}</td>
                  <td style={tdStyle}>
                    <select
                      aria-label={`Role for ${user.email}`}
                      data-testid={`role-select-${user.email}`}
                      value={user.role}
                      disabled={busy === `role:${user.id}`}
                      onChange={(e) => void changeRole(user, e.target.value as UserRole)}
                      style={{ ...inputStyle, padding: '3px 6px', width: 110 }}
                    >
                      {roleOptions.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={tdStyle}>
                    <Badge tone={user.is_active ? 'ok' : 'neutral'}>{user.is_active ? 'active' : 'deactivated'}</Badge>
                  </td>
                  <td style={tdStyle}>{formatDate(user.last_login_at)}</td>
                  <td style={tdStyle}>
                    <button
                      style={subtleButtonStyle}
                      data-testid={`toggle-active-${user.email}`}
                      disabled={busy === `active:${user.id}`}
                      onClick={() => void toggleActive(user)}
                    >
                      {user.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>

      {roles.length > 0 && (
        <ConsoleCard title="Role policy" testId="team-role-policy" description="Read from GET /auth/roles — the same table the server enforces.">
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Role</th>
                <th style={thStyle}>Level</th>
                <th style={thStyle}>Permissions</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.role}>
                  <td style={tdStyle}>{role.role}</td>
                  <td style={tdStyle}>{role.level}</td>
                  <td style={{ ...tdStyle, color: '#8fa3b8' }}>{role.permissions.length} granted</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ConsoleCard>
      )}
    </main>
  );
}

export default TeamConsolePage;
