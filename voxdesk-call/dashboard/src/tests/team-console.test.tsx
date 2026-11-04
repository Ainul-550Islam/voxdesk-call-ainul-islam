/**
 * dashboard/src/tests/team-console.test.tsx
 *
 * Team management has two distinct "add a person" paths in the backend
 * (`POST /api/team/users` writes an account; `POST /api/tenants/{id}/invitations`
 * emails a token). The page must not blur them, must not offer a role the
 * deployment does not define, and must keep a deactivated member visible once it
 * has been deactivated.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '../api/client';

const listTeamUsers = vi.fn();
const createTeamUser = vi.fn();
const changeUserRole = vi.fn();
const setUserActive = vi.fn();
const inviteToTenant = vi.fn();
const clientGet = vi.fn();

vi.mock('../api/team', () => ({
  USER_ROLES: ['owner', 'admin', 'manager', 'agent', 'viewer'],
  listTeamUsers: (...args: unknown[]) => listTeamUsers(...args),
  createTeamUser: (...args: unknown[]) => createTeamUser(...args),
  changeUserRole: (...args: unknown[]) => changeUserRole(...args),
  setUserActive: (...args: unknown[]) => setUserActive(...args),
  inviteToTenant: (...args: unknown[]) => inviteToTenant(...args),
  getTeamUser: vi.fn(),
  revokeInvitation: vi.fn(),
  listTeamAudit: vi.fn(),
}));

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return { ...actual, client: { ...actual.client, get: (...args: unknown[]) => clientGet(...args) } };
});

vi.mock('../lib/session', async () => {
  const actual = await vi.importActual<typeof import('../lib/session')>('../lib/session');
  return {
    ...actual,
    getAuthSession: () => ({
      user: { id: 'user-owner', email: 'owner@example.com', full_name: 'Owner', role: 'owner', tenant_id: 'tenant-001' },
      tenant: { id: 'tenant-001' },
      permissions: ['user:read', 'user:update'],
    }),
  };
});

import { TeamConsolePage } from '../pages/team/TeamConsolePage';

const MEMBER = {
  id: 'user-001',
  email: 'agent@example.com',
  full_name: 'Agent One',
  role: 'agent',
  is_active: true,
  tenant_id: 'tenant-001',
  last_login_at: '2026-10-20T08:00:00Z',
  created_at: '2026-09-01T08:00:00Z',
};

beforeEach(() => {
  for (const fn of [listTeamUsers, createTeamUser, changeUserRole, setUserActive, inviteToTenant, clientGet]) {
    fn.mockReset();
  }
  clientGet.mockResolvedValue({
    roles: [
      { role: 'owner', level: 100, permissions: ['user:read', 'user:update', 'user:role_change'] },
      { role: 'agent', level: 20, permissions: ['call:read'] },
    ],
  });
});

describe('TeamConsolePage', () => {
  it('lists members with the role selector limited to the deployment policy', async () => {
    listTeamUsers.mockResolvedValue([MEMBER]);

    render(<TeamConsolePage />);

    await waitFor(() => expect(screen.getByTestId('team-row-agent@example.com')).toBeInTheDocument());
    const select = screen.getByTestId('role-select-agent@example.com');
    const options = Array.from(select.querySelectorAll('option')).map((option) => option.value);
    expect(options).toEqual(['owner', 'agent']);
    expect(screen.getByTestId('team-role-policy')).toHaveTextContent('Role policy');
  });

  it('changes a role and reflects the server response', async () => {
    listTeamUsers.mockResolvedValue([MEMBER]);
    changeUserRole.mockResolvedValue({ ...MEMBER, role: 'manager' });

    render(<TeamConsolePage />);
    await waitFor(() => expect(screen.getByTestId('role-select-agent@example.com')).toBeInTheDocument());

    // Only roles advertised by GET /auth/roles are selectable, so this exercises
    // the same values the server will accept.
    await userEvent.selectOptions(screen.getByTestId('role-select-agent@example.com'), 'owner');

    await waitFor(() => expect(changeUserRole).toHaveBeenCalledWith('user-001', 'owner'));
    await waitFor(() =>
      expect(screen.getByTestId('team-notice')).toHaveTextContent('agent@example.com is now manager'),
    );
  });

  it('invites by email and shows the one-time token notice', async () => {
    listTeamUsers.mockResolvedValue([]);
    inviteToTenant.mockResolvedValue({
      id: 'inv-001',
      status: 'pending',
      expires_at: '2026-11-11T00:00:00Z',
      scope: 'tenant',
      invitation_token: 'tok_once',
    });

    render(<TeamConsolePage />);
    await waitFor(() => expect(screen.getByText('No members')).toBeInTheDocument());

    await userEvent.type(screen.getByTestId('team-email'), 'new@example.com');
    await userEvent.click(screen.getByTestId('team-submit'));

    await waitFor(() => expect(inviteToTenant).toHaveBeenCalledWith('tenant-001', 'new@example.com', 'agent'));
    await waitFor(() => expect(screen.getByTestId('team-notice')).toHaveTextContent('one-time token'));
  });

  it('requires a password of the documented length before creating an account directly', async () => {
    listTeamUsers.mockResolvedValue([]);

    render(<TeamConsolePage />);
    await waitFor(() => expect(screen.getByText('No members')).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('mode-create'));
    await userEvent.type(screen.getByTestId('team-email'), 'direct@example.com');
    await userEvent.click(screen.getByTestId('team-submit'));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/at least 8 characters/i));
    expect(createTeamUser).not.toHaveBeenCalled();
  });

  it('deactivates a member and keeps the row visible', async () => {
    listTeamUsers.mockResolvedValue([MEMBER]);
    setUserActive.mockResolvedValue({ ...MEMBER, is_active: false });

    render(<TeamConsolePage />);
    await waitFor(() => expect(screen.getByTestId('toggle-active-agent@example.com')).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('toggle-active-agent@example.com'));

    await waitFor(() => expect(setUserActive).toHaveBeenCalledWith('user-001', false));
    await waitFor(() => expect(screen.getByTestId('team-row-agent@example.com')).toHaveTextContent('deactivated'));
  });

  it('explains a 403 on the member list', async () => {
    listTeamUsers.mockRejectedValue(new ApiError('FORBIDDEN', 403, 'Forbidden'));

    render(<TeamConsolePage />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/permission/i));
  });
});
