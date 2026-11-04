/**
 * dashboard/src/components/layout/ConsoleStates.tsx
 * The four states every console page must render, in one place.
 *
 * Each new console page has to answer the same questions — is it loading, did it
 * fail, is it empty, is this account allowed to see it — and answering them
 * twelve times in twelve slightly different ways is how a console ends up with
 * eleven spinners and one blank screen. `describeError` also maps the shared
 * `ApiError` codes (401/403/NOT_CONFIGURED/…) to a message an operator can act
 * on, so a 403 never renders as "Something went wrong".
 */
import React from 'react';
import { ApiError } from '../../api/client';

export interface ConsoleCardProps {
  title?: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
}

export function ConsoleCard({ title, description, actions, children, testId }: ConsoleCardProps) {
  return (
    <section
      data-testid={testId}
      style={{
        background: '#0e141c',
        border: '1px solid #1d2836',
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
      }}
    >
      {(title || actions) && (
        <header style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
          <div>
            {title && <h2 style={{ margin: 0, fontSize: 15, color: '#e9eef5' }}>{title}</h2>}
            {description && <p style={{ margin: '4px 0 0', fontSize: 12, color: '#8fa3b8' }}>{description}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" data-testid="console-loading" style={{ padding: 24, color: '#8fa3b8', fontSize: 13 }}>
      {label}
    </div>
  );
}

export interface ConsoleErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  /** Shown instead of the raw message for 403 when the caller wants custom copy. */
  forbiddenHint?: string;
}

/**
 * Turn anything thrown by `apiRequest` into an operator-facing explanation.
 *
 * Returns `null` when there is nothing to show, so pages can pass the raw
 * caught value straight through.
 */
export function describeError(error: unknown, forbiddenHint?: string): { kind: 'unauthorized' | 'forbidden' | 'not_configured' | 'error'; message: string } | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return { kind: 'unauthorized', message: 'Your session has expired. Sign in again to continue.' };
    }
    if (error.status === 403) {
      return { kind: 'forbidden', message: forbiddenHint ?? 'This account does not have permission to view this data.' };
    }
    if (error.code === 'NOT_CONFIGURED') {
      return { kind: 'not_configured', message: `${error.message} This capability is not configured for this deployment.` };
    }
    if (error.code === 'RATE_LIMITED') {
      return { kind: 'error', message: 'Too many requests. Wait a moment and retry.' };
    }
    return { kind: 'error', message: error.message };
  }
  if (error instanceof Error) return { kind: 'error', message: error.message };
  return { kind: 'error', message: String(error) };
}

export function ErrorState({ error, onRetry, forbiddenHint }: ConsoleErrorStateProps) {
  const described = describeError(error, forbiddenHint);
  if (!described) return null;
  return (
    <div
      role="alert"
      data-testid={`console-error-${described.kind}`}
      style={{
        background: described.kind === 'forbidden' ? '#2a1f0b' : '#2a1010',
        border: `1px solid ${described.kind === 'forbidden' ? '#7a5a12' : '#7a2222'}`,
        borderRadius: 10,
        padding: 12,
        marginBottom: 12,
        color: '#f5dede',
        fontSize: 13,
      }}
    >
      <strong style={{ textTransform: 'uppercase', fontSize: 11, letterSpacing: 0.6 }}>
        {described.kind.replace('_', ' ')}
      </strong>
      <div style={{ marginTop: 4 }}>{described.message}</div>
      {onRetry && described.kind !== 'forbidden' && described.kind !== 'unauthorized' && (
        <button onClick={onRetry} style={{ marginTop: 8, padding: '4px 10px', borderRadius: 6, border: '1px solid #7a2222', background: 'transparent', color: '#f5dede', cursor: 'pointer', fontSize: 12 }}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div data-testid="console-empty" style={{ padding: 20, border: '1px dashed #24313f', borderRadius: 10, color: '#8fa3b8', fontSize: 13 }}>
      <div style={{ color: '#c8d6e5', fontWeight: 600 }}>{title}</div>
      {detail && <div style={{ marginTop: 4 }}>{detail}</div>}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block', marginBottom: 10, fontSize: 12, color: '#8fa3b8' }}>
      <span style={{ display: 'block', marginBottom: 4 }}>{label}</span>
      {children}
    </label>
  );
}

export const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '6px 8px',
  borderRadius: 6,
  border: '1px solid #24313f',
  background: '#0a0f15',
  color: '#e9eef5',
  fontSize: 13,
};

export const buttonStyle: React.CSSProperties = {
  padding: '6px 12px',
  borderRadius: 6,
  border: '1px solid #2f6fed',
  background: '#2f6fed',
  color: '#fff',
  fontSize: 12,
  cursor: 'pointer',
};

export const subtleButtonStyle: React.CSSProperties = {
  ...buttonStyle,
  background: 'transparent',
  border: '1px solid #24313f',
  color: '#c8d6e5',
};

export const tableStyle: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 };
export const thStyle: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #1d2836', color: '#8fa3b8', fontWeight: 600 };
export const tdStyle: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid #141c26', color: '#dbe6f1' };

/** ISO timestamp → locale string, or an em dash when the field is empty. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

export function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'bad' }) {
  const palette: Record<string, { bg: string; fg: string; border: string }> = {
    neutral: { bg: '#141c26', fg: '#c8d6e5', border: '#24313f' },
    ok: { bg: '#0f2418', fg: '#7ee2a8', border: '#1d4d31' },
    warn: { bg: '#2a1f0b', fg: '#f0c674', border: '#7a5a12' },
    bad: { bg: '#2a1010', fg: '#f2a1a1', border: '#7a2222' },
  };
  const tone_ = palette[tone];
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '1px 8px',
        borderRadius: 999,
        fontSize: 10.5,
        letterSpacing: 0.4,
        textTransform: 'uppercase',
        background: tone_.bg,
        color: tone_.fg,
        border: `1px solid ${tone_.border}`,
      }}
    >
      {children}
    </span>
  );
}

/**
 * Horizontal labelled bars for a small count map (status, direction, funnel).
 *
 * `max` is passed in rather than derived so several charts in one card share a
 * scale — two independently normalised charts side by side invite the reader to
 * compare bar lengths that are not comparable.
 */
export function SpanBars({ data, max }: { data: Array<[string, number]>; max: number }) {
  if (data.length === 0) {
    return <p style={{ fontSize: 12, color: '#8fa3b8', margin: 0 }}>No data in this window.</p>;
  }
  const ceiling = max > 0 ? max : 1;
  return (
    <div>
      {data.map(([label, value]) => (
        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{ width: 120, fontSize: 11.5, color: '#8fa3b8', textTransform: 'capitalize' }}>
            {label.replace(/_/g, ' ')}
          </span>
          <span style={{ flex: 1, height: 8, borderRadius: 4, background: '#141c26', overflow: 'hidden' }}>
            <span
              style={{
                display: 'block',
                height: 8,
                width: `${Math.max(0, Math.min(100, (value / ceiling) * 100))}%`,
                background: '#2f6fed',
                borderRadius: 4,
              }}
            />
          </span>
          <span style={{ width: 56, textAlign: 'right', fontSize: 11.5, color: '#dbe6f1' }}>{value}</span>
        </div>
      ))}
    </div>
  );
}
