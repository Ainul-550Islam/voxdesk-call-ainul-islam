/**
 * Tenant/environment audit explorer.
 *
 * The server enforces audit:read, tenant ownership, environment membership,
 * filtering, pagination and redaction. Free-text search remains explicitly
 * local to the returned page; it is never presented as a server-wide search.
 * The log is view-only. Export and mutation controls are not shown because
 * there is no verified export or edit contract for this page.
 */
import { useMemo, useState } from 'react'

import {
  Alert, AsyncSection, DataTable, EmptyState, StatCard,
} from '../components/ui'
import { listAudit, listUsers } from '../lib/api'
import { formatDateTime, formatNumber, humanise } from '../lib/format'
import { useApi } from '../lib/hooks'
import { PERMISSIONS as P } from '../lib/permissions'

const LIMITS = [50, 100, 250, 500]

/**
 * Keys whose values must never be rendered.
 *
 * Matched case-insensitively against the whole key path, so a nested
 * `{"config": {"api_key": ...}}` is caught too. Deliberately broad: a false
 * positive costs one hidden debug value, a false negative prints a secret.
 */
const SECRET_KEY = new RegExp([
  'secret', 'token', 'password', 'passwd', 'credential', 'api[-_]?key',
  'apikey', 'private', 'signature', 'signing', 'authorization', 'auth[-_]?header',
  'session[-_]?id', 'cookie', 'encryption', 'cipher', 'salt', 'hash',
  'client[-_]?secret', 'access[-_]?key', 'bearer',
].join('|'), 'i')

/** Values that look like a credential regardless of their key. */
const SECRET_VALUE = [
  /^sk_(live|test)_[A-Za-z0-9]+/,        // Stripe secret key
  /^whsec_[A-Za-z0-9]+/,                 // Stripe webhook secret
  /^rk_(live|test)_[A-Za-z0-9]+/,        // Stripe restricted key
  /^ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./,  // JWT
  /^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{20,}/, // bcrypt hash
  /^(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}/,  // GitHub token
  /^xox[baprs]-[A-Za-z0-9-]{10,}/,       // Slack token
  /^AKIA[0-9A-Z]{16}$/,                  // AWS access key id
]

const REDACTED = '[redacted]'

/**
 * Recursively mask secret-looking entries in an arbitrary JSON value.
 *
 * Depth- and size-capped: `detail` is attacker-influenceable in principle
 * (a crafted request path lands in an AUTHZ_DENIED row), so a pathological
 * structure must not be able to hang the render.
 */
export function redact(value, key = '', depth = 0) {
  if (depth > 6) return '[too deep]'

  if (key && SECRET_KEY.test(key)) return REDACTED

  if (typeof value === 'string') {
    return SECRET_VALUE.some((rx) => rx.test(value)) ? REDACTED : value
  }
  if (value === null || typeof value !== 'object') return value

  if (Array.isArray(value)) {
    return value.slice(0, 50).map((item) => redact(item, key, depth + 1))
  }
  return Object.fromEntries(
    Object.entries(value).slice(0, 50)
      .map(([k, v]) => [k, redact(v, k, depth + 1)])
  )
}

/**
 * How each real `AuditAction` should read.
 *
 * Tone is driven only by the action itself -- a real field. There is no risk
 * score, no severity heuristic, and nothing inferred about how "dangerous"
 * an event was.
 */
const ACTION_TONE = {
  login_failure: 'warn',
  authz_denied: 'danger',
  user_deactivated: 'warn',
  role_changed: 'warn',
  password_changed: 'warn',
  billing_payment_failed: 'danger',
  user_created: 'info',
  user_reactivated: 'info',
  login_success: 'ok',
  logout: 'muted',
  token_refresh: 'muted',
}

/** Coarse grouping for the filter, built from the action strings themselves. */
function categoryOf(action) {
  if (action.startsWith('billing_')) return 'Billing'
  if (action.startsWith('integration_') || action.startsWith('calendar_')) {
    return 'Integrations'
  }
  if (action.startsWith('appointment_')) return 'Appointments'
  if (action.startsWith('user_') || action === 'role_changed') return 'Team'
  return 'Sign-in and access'
}

export default function Audit({ me, can }) {
  const timezone = me.tenant.timezone
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [action, setAction] = useState('')
  const [actorId, setActorId] = useState('')
  const [environmentId, setEnvironmentId] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [resourceId, setResourceId] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [expanded, setExpanded] = useState(() => new Set())

  const toIso = (value) => {
    if (!value) return undefined
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
  }

  const events = useApi(() => listAudit({
    limit,
    offset,
    event_type: action,
    actor_id: actorId,
    environment_id: environmentId,
    resource_type: resourceType,
    resource_id: resourceId,
    start: toIso(from),
    end: toIso(to),
  }), [limit, offset, action, actorId, environmentId, resourceType, resourceId, from, to])

  const mayReadUsers = can(P.USER_READ)
  const users = useApi(() => (mayReadUsers ? listUsers() : Promise.resolve([])), [mayReadUsers])
  const emailById = useMemo(() => {
    const map = new Map()
    for (const user of users.data ?? []) map.set(user.id, user.email)
    return map
  }, [users.data])

  const page = events.data && !Array.isArray(events.data) ? events.data : null
  const rows = page?.items ?? (Array.isArray(events.data) ? events.data : [])
  const total = Number(page?.total ?? rows.length)
  const hasMore = Boolean(page?.has_more)
  const actions = useMemo(
    () => page?.event_types ?? [...new Set(rows.map((row) => row.event_type || row.action).filter(Boolean))].sort(),
    [page?.event_types, rows]
  )

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return rows.filter((row) => {
      if (!needle) return true
      const target = emailById.get(row.target_user_id) ?? row.target_user_id ?? ''
      return [row.event_type, row.action, row.actor_type, row.actor_email, row.ip_address, target,
        row.environment_id, row.resource_type, row.resource_id, row.request_id, row.result,
        JSON.stringify(redact(row.detail))]
        .join(' ').toLowerCase().includes(needle)
    })
  }, [rows, search, emailById])

  const resetPage = (setter) => (value) => {
    setter(value)
    setOffset(0)
  }

  const toggle = (id) => setExpanded((previous) => {
    const next = new Set(previous)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const columns = useMemo(() => [
    {
      key: 'when', header: 'When',
      render: (row) => (
        <span style={{ whiteSpace: 'nowrap' }}>
          {formatDateTime(row.created_at, timezone)}
        </span>
      ),
    },
    {
      key: 'action', header: 'Event',
      render: (row) => {
        const eventName = row.event_type || row.action
        return (
          <span className={`badge badge--${ACTION_TONE[row.action] ?? 'muted'}`}>
            {humanise(eventName)}
          </span>
        )
      },
    },
    {
      key: 'actor', header: 'Who',
      render: (row) => (
        <div>
          {row.actor_email
            ? <span style={{ overflowWrap: 'anywhere' }}>{row.actor_email}</span>
            : <span className="muted">{humanise(row.actor_type || 'system')}</span>}
          {row.actor_email && <div className="muted" style={{ fontSize: 11 }}>{humanise(row.actor_type || 'human')}</div>}
        </div>
      ),
    },
    {
      key: 'target', header: 'Affected',
      render: (row) => {
        if (!row.target_user_id) return <span className="muted">—</span>
        const email = emailById.get(row.target_user_id)
        return email
          ? <span style={{ overflowWrap: 'anywhere' }}>{email}</span>
          : (
            <span className="muted" title={row.target_user_id}>
              {/* A deleted or foreign id stays a raw id rather than a guess. */}
              {row.target_user_id.slice(0, 8)}…
            </span>
          )
      },
    },
    {
      key: 'scope', header: 'Environment / resource',
      render: (row) => (
        <div style={{ minWidth: 180 }}>
          {row.environment_id && <div><span className="muted">Environment </span><code style={{ fontSize: 11 }}>{row.environment_id}</code></div>}
          {row.resource_type && <div><span className="muted">{humanise(row.resource_type)} </span>{row.resource_id ? <code style={{ fontSize: 11 }}>{row.resource_id}</code> : null}</div>}
          {!row.environment_id && !row.resource_type && <span className="muted">—</span>}
        </div>
      ),
    },
    {
      key: 'result', header: 'Result',
      render: (row) => <span className={`badge badge--${row.result === 'success' ? 'ok' : row.result === 'denied' || row.result === 'failure' ? 'warn' : 'muted'}`}>{humanise(row.result || 'success')}</span>,
    },
    {
      key: 'request', header: 'Request ID',
      render: (row) => row.request_id ? <code style={{ fontSize: 11 }}>{row.request_id}</code> : <span className="muted">—</span>,
    },
    {
      key: 'ip', header: 'IP address',
      render: (row) => (row.ip_address
        ? <code style={{ fontSize: 12 }}>{row.ip_address}</code>
        : <span className="muted">—</span>),
    },
    {
      key: 'detail', header: 'Details',
      render: (row) => {
        const detail = row.detail
        const hasDetail = detail && Object.keys(detail).length > 0
        if (!hasDetail) return <span className="muted">—</span>
        const isOpen = expanded.has(row.id)
        return (
          <div>
            <button
              type="button"
              className="btn btn--link btn--small"
              aria-expanded={isOpen}
              onClick={() => toggle(row.id)}
            >
              {isOpen ? 'Hide' : 'Show'}
            </button>
            {/* Expanded in place: `DataTable` has no expanded-row slot, and
                adding one would change a component five other pages use. */}
            {isOpen && <Detail detail={detail} />}
          </div>
        )
      },
    },
  ], [timezone, emailById, expanded])

  return (
    <>
      <div className="page__header">
        <div>
          <h1>Audit log</h1>
          <p className="page__description">
            Tenant-scoped security and operational events. Application users
            have read-only access; database administrators retain their normal
            infrastructure-level authority.
          </p>
        </div>
      </div>

      <div className="grid grid--stats">
        <StatCard
          label="Events matching filters"
          value={formatNumber(total)}
          hint={`Showing ${formatNumber(rows.length)} on this page`}
        />
        <StatCard
          label="Sign-in failures on page"
          value={formatNumber(rows.filter((r) => r.action === 'login_failure' || r.event_type === 'failed_login').length)}
          hint="Current page only"
        />
        <StatCard
          label="Permission denials on page"
          value={formatNumber(rows.filter((r) => r.action === 'authz_denied' || r.event_type === 'authorization_denied').length)}
          hint="Current page only"
        />
      </div>

      <section className="card" aria-label="Audit events">
        <div className="card__header">
          <div>
            <h3>Events</h3>
            <p className="muted" style={{ margin: '2px 0 0', fontSize: 13 }}>
              Newest first, in {timezone}.
            </p>
          </div>
          <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <label className="sr-only" htmlFor="audit-search">Search this page</label>
            <input
              id="audit-search"
              className="input"
              type="search"
              placeholder="Search this page"
              style={{ width: 'auto' }}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />

            <label className="sr-only" htmlFor="audit-action">Filter by event</label>
            <select
              id="audit-action"
              className="select"
              style={{ width: 'auto' }}
              value={action}
              onChange={(event) => resetPage(setAction)(event.target.value)}
            >
              <option value="">All events</option>
              {actions.map((value) => (
                <option key={value} value={value}>
                  {humanise(value)} · {categoryOf(value)}
                </option>
              ))}
            </select>

            {mayReadUsers && (
              <>
                <label className="sr-only" htmlFor="audit-actor">Filter by actor</label>
                <select
                  id="audit-actor"
                  className="select"
                  style={{ width: 'auto' }}
                  value={actorId}
                  onChange={(event) => resetPage(setActorId)(event.target.value)}
                >
                  <option value="">All actors</option>
                  {(users.data ?? []).map((user) => (
                    <option key={user.id} value={user.id}>{user.email}</option>
                  ))}
                </select>
              </>
            )}

            <label className="sr-only" htmlFor="audit-from">From date and time</label>
            <input
              id="audit-from"
              className="input"
              type="datetime-local"
              aria-label="From date and time"
              value={from}
              onChange={(event) => resetPage(setFrom)(event.target.value)}
            />
            <label className="sr-only" htmlFor="audit-to">To date and time</label>
            <input
              id="audit-to"
              className="input"
              type="datetime-local"
              aria-label="To date and time"
              value={to}
              onChange={(event) => resetPage(setTo)(event.target.value)}
            />
            <label className="sr-only" htmlFor="audit-environment">Environment ID</label>
            <input
              id="audit-environment"
              className="input"
              type="text"
              placeholder="Environment UUID"
              aria-label="Environment UUID"
              style={{ width: 180 }}
              value={environmentId}
              onChange={(event) => resetPage(setEnvironmentId)(event.target.value.trim())}
            />
            <label className="sr-only" htmlFor="audit-resource-type">Resource type</label>
            <input
              id="audit-resource-type"
              className="input"
              type="search"
              placeholder="Resource type"
              aria-label="Resource type"
              style={{ width: 140 }}
              value={resourceType}
              onChange={(event) => resetPage(setResourceType)(event.target.value)}
            />
            <label className="sr-only" htmlFor="audit-resource-id">Resource ID</label>
            <input
              id="audit-resource-id"
              className="input"
              type="search"
              placeholder="Resource ID"
              aria-label="Resource ID"
              style={{ width: 160 }}
              value={resourceId}
              onChange={(event) => resetPage(setResourceId)(event.target.value)}
            />
            <label className="sr-only" htmlFor="audit-limit">Events per page</label>
            <select
              id="audit-limit"
              className="select"
              style={{ width: 'auto' }}
              value={limit}
              onChange={(event) => { setLimit(Number(event.target.value)); setOffset(0) }}
            >
              {LIMITS.map((value) => (
                <option key={value} value={value}>{value} per page</option>
              ))}
            </select>
          </div>
        </div>

        <div className="card__body">
          <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
            Event, actor, time, environment and resource filters are applied by
            the server. Free-text search scans only the currently loaded page.
          </p>

          {hasMore && (
            <Alert tone="info">
              More matching events are available. Use Next to continue through
              the server-paginated results.
            </Alert>
          )}

          <AsyncSection
            loading={events.loading}
            error={events.error}
            onRetry={events.reload}
            resource="the audit log"
            isEmpty={Boolean(events.data) && rows.length === 0}
            empty={
              <EmptyState
                icon="?"
                title="No events match these filters"
                description="Try a wider time range or clear one of the server-side filters."
              />
            }
          >
            {rows.length > 0 && (
              visible.length > 0 ? (
                <DataTable
                  caption="Audit events"
                  columns={columns}
                  rows={visible}
                  keyOf={(row) => row.id}
                />
              ) : (
                <EmptyState
                  icon="?"
                  title="No matching events"
                  description="Try a different search or event type."
                />
              )
            )}
          </AsyncSection>
          {rows.length > 0 && (
            <div className="row" style={{ justifyContent: 'space-between', marginTop: 16 }}>
              <span className="muted" aria-live="polite">
                Showing {formatNumber(offset + 1)}–{formatNumber(offset + rows.length)} of {formatNumber(total)}
                {' · '}page {Math.floor(offset / limit) + 1}
              </span>
              <div className="row" style={{ gap: 8 }}>
                <button
                  type="button"
                  className="btn btn--small"
                  disabled={offset === 0 || events.loading}
                  onClick={() => setOffset((value) => Math.max(0, value - limit))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="btn btn--small"
                  disabled={!hasMore || events.loading}
                  onClick={() => setOffset((value) => value + limit)}
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      </section>
    </>
  )
}

/**
 * The `detail` blob, redacted and rendered as text.
 *
 * `JSON.stringify` output goes in as a React child, so it is escaped -- a
 * detail value containing markup is displayed, never parsed.
 */
function Detail({ detail }) {
  const safe = useMemo(() => redact(detail), [detail])
  return (
    <div className="kv" style={{ gridTemplateColumns: 'minmax(120px,200px) 1fr' }}>
      {Object.entries(safe).map(([key, value]) => (
        <Row key={key} label={key} value={value} />
      ))}
    </div>
  )
}

function Row({ label, value }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return (
    <>
      <dt>{humanise(label)}</dt>
      <dd style={{ overflowWrap: 'anywhere' }}>
        <code style={{ fontSize: 12 }}>{text}</code>
      </dd>
    </>
  )
}