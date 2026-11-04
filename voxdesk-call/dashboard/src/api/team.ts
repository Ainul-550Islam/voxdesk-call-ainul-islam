/**
 * dashboard/src/api/team.ts
 * Team members, invitations and role (RBAC) management.
 *
 * Route sources (verified):
 *   app/api/team_routes.py               → prefix `/api/team`
 *       GET  /api/team/users                     (list, `include_inactive`)
 *       POST /api/team/users                     (create: email, password, full_name?, role?)
 *       GET  /api/team/users/{user_id}
 *       PATCH /api/team/users/{user_id}/role     (body `RoleChangeIn`)
 *       PATCH /api/team/users/{user_id}/active   (body `ActiveIn`)
 *       GET  /api/team/audit
 *   app/api/tenant_membership_routes.py  → prefix `/api/tenants`
 *       POST /api/tenants/{tenant_id}/invitations                       (`InviteBody`)
 *       POST /api/tenants/{tenant_id}/invitations/{invitation_id}/revoke
 *
 * Invitations live under the tenant-membership router, not under `/api/team`,
 * so an invite is a tenant-scoped call. `UserRole` is the backend enum
 * (owner/admin/manager/agent/viewer) — the client never invents a role string.
 */
import { client } from './client';

export type UserRole = 'owner' | 'admin' | 'manager' | 'agent' | 'viewer';

export const USER_ROLES: UserRole[] = ['owner', 'admin', 'manager', 'agent', 'viewer'];

export interface TeamUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  tenant_id: string;
  is_active: boolean;
  created_at?: string | null;
  last_login_at?: string | null;
}

export interface CreateUserInput {
  email: string;
  password: string;
  full_name?: string;
  role?: UserRole;
}

export interface Invitation {
  id: string;
  status: string;
  expires_at: string;
  scope: string;
  invitation_token?: string | null;
}

export interface TeamAuditEvent {
  [key: string]: unknown;
}

export function listTeamUsers(includeInactive = false): Promise<TeamUser[]> {
  return client.get<TeamUser[]>(`/api/team/users?include_inactive=${includeInactive ? 'true' : 'false'}`);
}

export function getTeamUser(userId: string): Promise<TeamUser> {
  return client.get<TeamUser>(`/api/team/users/${encodeURIComponent(userId)}`);
}

/**
 * Create a member account directly.
 *
 * `/api/team/users` takes a password, so this is the "add a teammate now" path.
 * The invite path (below) sends a token instead; the console offers both and
 * says which one it is using.
 */
export function createTeamUser(input: CreateUserInput): Promise<TeamUser> {
  return client.post<TeamUser>('/api/team/users', input);
}

export function changeUserRole(userId: string, role: UserRole): Promise<TeamUser> {
  return client.patch<TeamUser>(`/api/team/users/${encodeURIComponent(userId)}/role`, { role });
}

export function setUserActive(userId: string, isActive: boolean): Promise<TeamUser> {
  return client.patch<TeamUser>(`/api/team/users/${encodeURIComponent(userId)}/active`, { is_active: isActive });
}

export function inviteToTenant(tenantId: string, email: string, role: UserRole): Promise<Invitation> {
  return client.post<Invitation>(`/api/tenants/${encodeURIComponent(tenantId)}/invitations`, { email, role });
}

export function revokeInvitation(tenantId: string, invitationId: string): Promise<Invitation> {
  return client.post<Invitation>(
    `/api/tenants/${encodeURIComponent(tenantId)}/invitations/${encodeURIComponent(invitationId)}/revoke`,
  );
}

export function listTeamAudit(limit = 50): Promise<TeamAuditEvent[]> {
  return client.get<TeamAuditEvent[]>(`/api/team/audit?limit=${encodeURIComponent(String(limit))}`);
}
