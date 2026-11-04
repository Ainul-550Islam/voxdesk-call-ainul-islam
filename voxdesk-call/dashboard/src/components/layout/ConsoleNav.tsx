/**
 * dashboard/src/components/layout/ConsoleNav.tsx
 * Sidebar for the authenticated operator console.
 *
 * Visibility is role-aware and permission-aware:
 *
 *   1. When the caller supplies the server's permission list (`/auth/me` →
 *      `permissions`), each link is shown only if its own permission is granted.
 *      That keeps the sidebar honest when the RBAC policy changes.
 *   2. When no permission list is available (older console shells, tests,
 *      storybook-style previews) the component falls back to the role → link
 *      table below, which mirrors `app/auth/permissions.py`.
 *
 * Neither path is security. Hiding a link stops a viewer from opening a page
 * that would immediately 403; every page still calls a real endpoint that
 * re-checks the permission server-side.
 */
import React from 'react';

export type ConsoleRole = 'owner' | 'admin' | 'manager' | 'agent' | 'viewer';

export interface ConsoleNavItem {
  key: string;
  label: string;
  path: string;
  /** Permission required, using the identifiers from lib/permissions.js. */
  permission: string;
  description: string;
  /** Roles that see the link when no permission list was supplied. */
  roles: ConsoleRole[];
}

export interface ConsoleNavSection {
  title: string;
  items: ConsoleNavItem[];
}

const ALL_ROLES: ConsoleRole[] = ['owner', 'admin', 'manager', 'agent', 'viewer'];
const MANAGERS: ConsoleRole[] = ['owner', 'admin', 'manager'];
const ADMINS: ConsoleRole[] = ['owner', 'admin'];

export const CONSOLE_SECTIONS: ConsoleNavSection[] = [
  {
    title: 'Operate',
    items: [
      { key: 'campaigns', label: 'Campaigns', path: '/dashboard/campaigns', permission: 'campaign:read', description: 'Outbound campaigns, audiences and pacing', roles: MANAGERS },
      { key: 'analytics', label: 'Analytics', path: '/dashboard/analytics-console', permission: 'analytics:read', description: 'Call, conversion and usage analytics', roles: ALL_ROLES },
      { key: 'knowledge', label: 'Knowledge base', path: '/dashboard/knowledge-base', permission: 'knowledge:read', description: 'Workspace documents, indexing and retrieval', roles: ALL_ROLES },
      { key: 'integrations', label: 'Integrations', path: '/dashboard/integrations-console', permission: 'integration:read', description: 'Authenticated CRM connections and sync runs', roles: MANAGERS },
    ],
  },
  {
    title: 'Workspace',
    items: [
      { key: 'team', label: 'Team', path: '/dashboard/team', permission: 'user:read', description: 'Members, invitations and roles', roles: MANAGERS },
      { key: 'billing', label: 'Billing', path: '/dashboard/billing', permission: 'billing:read', description: 'Plan, usage, invoices and payment method', roles: ADMINS },
      { key: 'workspace-settings', label: 'Workspace settings', path: '/dashboard/settings/workspace', permission: 'tenant:read', description: 'Name, timezone and security policy', roles: ADMINS },
      { key: 'identity-settings', label: 'Identity settings', path: '/dashboard/settings/identity', permission: 'identity:read', description: 'SSO, SCIM, API keys, service accounts, domains', roles: ADMINS },
    ],
  },
  {
    title: 'Govern',
    items: [
      { key: 'webhooks', label: 'Webhooks', path: '/dashboard/webhooks', permission: 'integration:read', description: 'Endpoints, delivery log, replay and secrets', roles: MANAGERS },
      { key: 'audit', label: 'Audit log', path: '/dashboard/audit-log', permission: 'audit:read', description: 'Immutable audit events with CSV export', roles: MANAGERS },
    ],
  },
];

export interface ConsoleNavProps {
  /** Current path, e.g. `window.location.pathname`. */
  currentPath: string;
  /** Permission identifiers from `/auth/me`. */
  permissions?: string[];
  /** Fallback when permissions are unavailable. */
  role?: ConsoleRole;
  /** Called with the target path; defaults to a normal same-origin navigation. */
  onNavigate?: (path: string) => void;
}

export function isItemVisible(item: ConsoleNavItem, permissions?: string[], role?: ConsoleRole): boolean {
  if (permissions && permissions.length > 0) return permissions.includes(item.permission);
  if (role) return item.roles.includes(role);
  return false;
}

export function visibleSections(permissions?: string[], role?: ConsoleRole): ConsoleNavSection[] {
  return CONSOLE_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => isItemVisible(item, permissions, role)),
  })).filter((section) => section.items.length > 0);
}

export function ConsoleNav({ currentPath, permissions, role, onNavigate }: ConsoleNavProps) {
  const sections = visibleSections(permissions, role);

  const go = (event: React.MouseEvent<HTMLAnchorElement>, path: string) => {
    if (!onNavigate) return; // let the browser handle it (the app shell pushes state)
    event.preventDefault();
    onNavigate(path);
  };

  return (
    <nav
      aria-label="Operator console"
      data-testid="console-nav"
      style={{ width: 232, flexShrink: 0, borderRight: '1px solid #1d2836', background: '#080c12', padding: '16px 10px', minHeight: '100%' }}
    >
      {sections.length === 0 && (
        <p style={{ fontSize: 12, color: '#8fa3b8', padding: '0 8px' }}>
          No console sections are available for this account.
        </p>
      )}
      {sections.map((section) => (
        <div key={section.title} style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 10.5, letterSpacing: 0.8, textTransform: 'uppercase', color: '#5d7186', padding: '0 8px 6px' }}>
            {section.title}
          </div>
          {section.items.map((item) => {
            const active = currentPath === item.path || currentPath.startsWith(`${item.path}/`);
            return (
              <a
                key={item.key}
                href={item.path}
                onClick={(event) => go(event, item.path)}
                aria-current={active ? 'page' : undefined}
                data-testid={`console-nav-${item.key}`}
                title={item.description}
                style={{
                  display: 'block',
                  padding: '7px 10px',
                  borderRadius: 8,
                  marginBottom: 2,
                  fontSize: 13,
                  textDecoration: 'none',
                  color: active ? '#ffffff' : '#a9bccf',
                  background: active ? '#1b3a6b' : 'transparent',
                }}
              >
                {item.label}
              </a>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

export default ConsoleNav;
