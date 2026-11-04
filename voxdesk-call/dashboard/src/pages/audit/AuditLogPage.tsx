/**
 * dashboard/src/pages/audit/AuditLogPage.tsx
 * Audit log: filter, paginate, export the loaded page set to CSV.
 *
 * Data: `api/audit.ts` → `GET /api/audit-trail/events`
 * (app/api/audit_trail_routes.py → app/api/v1/audit_routes.py::query_audit_events).
 *
 * The server caps the window at 366 days and `limit` at 500; both are enforced
 * here as well so the operator gets an explanation instead of a 422. The
 * `event_types` catalogue in the response drives the filter dropdown — the page
 * does not ship its own list of event names.
 *
 * CSV export covers exactly the rows that are loaded (the API has no CSV route);
 * the button says how many rows it will write so nobody mistakes it for a full
 * export.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AUDIT_PAGE_SIZE_MAX,
  AUDIT_RESULTS,
  AuditFilters,
  AuditPage,
  AuditResult,
  exportEventsCsv,
  listAuditEvents,
  validateWindow,
} from '../../api/audit';
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

const PAGE_SIZE = 50;

export function AuditLogPage() {
  const [page, setPage] = useState<AuditPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [filters, setFilters] = useState<AuditFilters>({ limit: PAGE_SIZE, offset: 0 });
  const [draft, setDraft] = useState<{ start: string; end: string; eventType: string; result: string; resourceType: string; actorId: string }>({
    start: '',
    end: '',
    eventType: '',
    result: '',
    resourceType: '',
    actorId: '',
  });
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async (next: AuditFilters) => {
    setLoading(true);
    setError(null);
    try {
      const result = await listAuditEvents(next);
      setPage(result);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filters);
  }, [load, filters]);

  function apply(event: React.FormEvent) {
    event.preventDefault();
    const windowProblem = validateWindow(draft.start || null, draft.end || null);
    if (windowProblem) {
      setFormError(windowProblem);
      return;
    }
    if (draft.actorId && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(draft.actorId)) {
      setFormError('The actor filter must be a UUID; the API rejects anything else with a 422.');
      return;
    }
    setFormError(null);
    setFilters({
      limit: PAGE_SIZE,
      offset: 0,
      start: draft.start ? new Date(draft.start).toISOString() : null,
      end: draft.end ? new Date(draft.end).toISOString() : null,
      eventType: draft.eventType || null,
      result: (draft.result as AuditResult) || null,
      resourceType: draft.resourceType || null,
      actorId: draft.actorId || null,
    });
  }

  function reset() {
    setDraft({ start: '', end: '', eventType: '', result: '', resourceType: '', actorId: '' });
    setFormError(null);
    setFilters({ limit: PAGE_SIZE, offset: 0 });
  }

  function exportCsv() {
    const rows = page?.items ?? [];
    if (rows.length === 0) return;
    const blob = new Blob([exportEventsCsv(rows)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `audit-events-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const items = page?.items ?? [];
  const total = page?.total ?? 0;
  const offset = filters.offset ?? 0;
  const canPrev = offset > 0;
  const canNext = page?.has_more ?? false;

  return (
    <main data-testid="audit-page" style={{ padding: 20, color: '#e9eef5' }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Audit log</h1>
      <p style={{ margin: '0 0 16px', fontSize: 12.5, color: '#8fa3b8' }}>
        Immutable, tenant-scoped audit events. Windows are capped at 366 days by the server.
      </p>

      <ErrorState error={error} onRetry={() => void load(filters)} />

      <ConsoleCard title="Filters" testId="audit-filters">
        <form onSubmit={apply}>
          {formError && (
            <div role="alert" data-testid="audit-form-error" style={{ background: '#2a1010', border: '1px solid #7a2222', borderRadius: 8, padding: 10, marginBottom: 10, fontSize: 12.5 }}>
              {formError}
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12 }}>
            <Field label="Start (local date/time)">
              <input style={inputStyle} data-testid="audit-start" type="datetime-local" value={draft.start} onChange={(e) => setDraft({ ...draft, start: e.target.value })} />
            </Field>
            <Field label="End (local date/time)">
              <input style={inputStyle} data-testid="audit-end" type="datetime-local" value={draft.end} onChange={(e) => setDraft({ ...draft, end: e.target.value })} />
            </Field>
            <Field label="Event type">
              <select style={inputStyle} data-testid="audit-event-type" value={draft.eventType} onChange={(e) => setDraft({ ...draft, eventType: e.target.value })}>
                <option value="">all event types</option>
                {(page?.event_types ?? []).map((eventType) => (
                  <option key={eventType} value={eventType}>
                    {eventType}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Result">
              <select style={inputStyle} value={draft.result} onChange={(e) => setDraft({ ...draft, result: e.target.value })}>
                <option value="">any result</option>
                {AUDIT_RESULTS.map((result) => (
                  <option key={result} value={result}>
                    {result}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Resource type">
              <input style={inputStyle} value={draft.resourceType} onChange={(e) => setDraft({ ...draft, resourceType: e.target.value })} placeholder="user, call, campaign…" />
            </Field>
            <Field label="Actor id (UUID)">
              <input style={inputStyle} value={draft.actorId} onChange={(e) => setDraft({ ...draft, actorId: e.target.value })} />
            </Field>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" style={buttonStyle} data-testid="audit-apply">
              Apply filters
            </button>
            <button type="button" style={subtleButtonStyle} onClick={reset}>
              Reset
            </button>
          </div>
        </form>
      </ConsoleCard>

      <ConsoleCard
        title={`Events (${total})`}
        testId="audit-results"
        description={`Showing ${items.length} of ${total} · limit ${Math.min(filters.limit ?? PAGE_SIZE, AUDIT_PAGE_SIZE_MAX)}`}
        actions={
          <div style={{ display: 'flex', gap: 6 }}>
            <button style={subtleButtonStyle} disabled={!canPrev} onClick={() => setFilters({ ...filters, offset: Math.max(0, offset - PAGE_SIZE) })}>
              Previous
            </button>
            <button style={subtleButtonStyle} disabled={!canNext} onClick={() => setFilters({ ...filters, offset: offset + PAGE_SIZE })} data-testid="audit-next">
              Next
            </button>
            <button style={buttonStyle} onClick={exportCsv} disabled={items.length === 0} data-testid="audit-export">
              Export {items.length} row{items.length === 1 ? '' : 's'} to CSV
            </button>
          </div>
        }
      >
        {loading ? (
          <LoadingState label="Loading audit events…" />
        ) : items.length === 0 ? (
          <EmptyState title="No events match" detail="Widen the window or clear a filter. An empty result is a real answer, not an error." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>When</th>
                <th style={thStyle}>Event</th>
                <th style={thStyle}>Action</th>
                <th style={thStyle}>Result</th>
                <th style={thStyle}>Actor</th>
                <th style={thStyle}>Resource</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {items.map((event) => (
                <React.Fragment key={event.id}>
                  <tr data-testid={`audit-row-${event.id}`}>
                    <td style={tdStyle}>{formatDate(event.created_at)}</td>
                    <td style={tdStyle}>{event.event_type}</td>
                    <td style={tdStyle}>{event.action}</td>
                    <td style={tdStyle}>
                      <Badge tone={event.result === 'success' ? 'ok' : event.result === 'denied' ? 'warn' : 'bad'}>{event.result ?? 'unknown'}</Badge>
                    </td>
                    <td style={tdStyle}>{event.actor_email || event.actor_id || '—'}</td>
                    <td style={tdStyle}>
                      {event.resource_type ?? '—'}
                      {event.resource_id ? `:${event.resource_id.slice(0, 8)}` : ''}
                    </td>
                    <td style={tdStyle}>
                      <button style={subtleButtonStyle} onClick={() => setExpanded(expanded === event.id ? null : event.id)}>
                        {expanded === event.id ? 'Hide' : 'Detail'}
                      </button>
                    </td>
                  </tr>
                  {expanded === event.id && (
                    <tr>
                      <td colSpan={7} style={{ ...tdStyle, background: '#0a0f15' }}>
                        <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap' }}>
                          {JSON.stringify(
                            {
                              id: event.id,
                              request_id: event.request_id,
                              ip_address: event.ip_address,
                              user_agent: event.user_agent,
                              environment_id: event.environment_id,
                              target_user_id: event.target_user_id,
                              detail: event.detail ?? {},
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </ConsoleCard>
    </main>
  );
}

export default AuditLogPage;
