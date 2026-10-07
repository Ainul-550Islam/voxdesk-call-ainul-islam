/**
 * dashboard/src/components/testing/TestRunStatus.tsx
 * Visual status badge distinguishing queued, running, passed, failed, error, cancelled, and not_run
 * without ever converting errors or empty runs into PASS.
 */

import React from 'react';
import type { TestRunStatus as TestRunStatusType } from '../../api/types/test-run';

export interface TestRunStatusProps {
  status: TestRunStatusType | string;
  isMockProvider?: boolean;
  errorCode?: string | null;
}

const STATUS_MAP: Record<
  string,
  { label: string; bg: string; fg: string; border: string }
> = {
  queued: {
    label: 'QUEUED',
    bg: 'rgba(148,163,184,0.14)',
    fg: '#94A3B8',
    border: 'rgba(148,163,184,0.35)',
  },
  running: {
    label: 'RUNNING',
    bg: 'rgba(59,130,246,0.16)',
    fg: '#60A5FA',
    border: 'rgba(59,130,246,0.4)',
  },
  passed: {
    label: 'PASS',
    bg: 'rgba(16,185,129,0.16)',
    fg: '#34D399',
    border: 'rgba(16,185,129,0.4)',
  },
  failed: {
    label: 'FAILED ASSERTION',
    bg: 'rgba(239,68,68,0.16)',
    fg: '#F87171',
    border: 'rgba(239,68,68,0.4)',
  },
  error: {
    label: 'ERROR',
    bg: 'rgba(245,158,11,0.16)',
    fg: '#FBBF24',
    border: 'rgba(245,158,11,0.4)',
  },
  cancelled: {
    label: 'CANCELLED',
    bg: 'rgba(148,163,184,0.14)',
    fg: '#CBD5E1',
    border: 'rgba(148,163,184,0.35)',
  },
  not_run: {
    label: 'NOT RUN',
    bg: 'rgba(100,116,139,0.14)',
    fg: '#94A3B8',
    border: 'rgba(100,116,139,0.35)',
  },
};

export function TestRunStatus({
  status,
  isMockProvider,
  errorCode,
}: TestRunStatusProps) {
  const normalized = String(status || 'not_run').toLowerCase();
  const style = STATUS_MAP[normalized] || STATUS_MAP.not_run;

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span
        data-testid={`test-run-status-${normalized}`}
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.04em',
          padding: '2px 8px',
          borderRadius: 999,
          background: style.bg,
          color: style.fg,
          border: `1px solid ${style.border}`,
        }}
      >
        {errorCode === 'NOT_CONFIGURED' ? 'NOT CONFIGURED' : style.label}
      </span>
      {isMockProvider && (
        <span
          data-testid="test-run-mock-provider-badge"
          style={{
            fontSize: 10,
            fontWeight: 600,
            padding: '2px 6px',
            borderRadius: 6,
            background: 'rgba(139,92,246,0.14)',
            color: '#C4B5FD',
            border: '1px solid rgba(139,92,246,0.35)',
          }}
        >
          DETERMINISTIC RUNTIME
        </span>
      )}
    </span>
  );
}

export default TestRunStatus;
