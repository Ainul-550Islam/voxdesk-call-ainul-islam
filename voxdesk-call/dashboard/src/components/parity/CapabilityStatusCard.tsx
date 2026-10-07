import React from 'react';
import type { CapabilityItem, ParityStatus } from '../../lib/parityApi';

const STATUS_COLORS: Record<ParityStatus, { color: string; background: string }> = {
  MISSING: { color: '#ffd4d4', background: '#4a1c25' },
  PARTIAL: { color: '#ffe3a6', background: '#49371a' },
  IMPLEMENTED: { color: '#c8e7ff', background: '#173650' },
  VERIFIED: { color: '#c6f2d4', background: '#173b2b' },
  PRODUCTION_READY: { color: '#c6f2d4', background: '#173b2b' },
  NOT_CONFIGURED: { color: '#d7dce6', background: '#303746' },
  RESOURCE_LIMITED: { color: '#ffd4ad', background: '#49301a' },
};

export function CapabilityStatusCard({ item }: { item: CapabilityItem }) {
  const style = STATUS_COLORS[item.status];

  return (
    <article
      style={{
        background: '#111923',
        border: '1px solid #293544',
        borderRadius: 14,
        padding: 18,
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <h3 style={{ margin: 0, fontSize: 16, lineHeight: 1.35 }}>{item.label}</h3>
        <span
          aria-label={`Status: ${item.status}`}
          style={{
            color: style.color,
            background: style.background,
            borderRadius: 999,
            padding: '4px 9px',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '.04em',
            whiteSpace: 'nowrap',
          }}
        >
          {item.status}
        </span>
      </div>
      <p style={{ color: '#bbc5d3', fontSize: 13, lineHeight: 1.55, margin: '12px 0' }}>
        {item.summary}
      </p>
      <details>
        <summary style={{ cursor: 'pointer', color: '#9fcfff', fontSize: 12 }}>
          Route evidence ({item.evidence_routes.length})
        </summary>
        {item.evidence_routes.length === 0 ? (
          <p style={{ color: '#aab5c4', fontSize: 12 }}>No matching registered API routes.</p>
        ) : (
          <ul style={{ paddingLeft: 18, color: '#c5cfdd', fontSize: 12, lineHeight: 1.65 }}>
            {item.evidence_routes.map((route) => (
              <li key={`${item.key}:${route.method}:${route.path}`}>
                <code>{route.method} {route.path}</code>
                <span style={{ display: 'block', color: '#8593a5' }}>{route.module}</span>
              </li>
            ))}
          </ul>
        )}
      </details>
      <p style={{ color: '#8f9cac', fontSize: 11, marginBottom: 0 }}>
        Evidence basis: registered routes only; this is not a verification result.
      </p>
    </article>
  );
}

export default CapabilityStatusCard;
