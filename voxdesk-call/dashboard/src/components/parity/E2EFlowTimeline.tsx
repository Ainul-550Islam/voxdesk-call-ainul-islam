import React from 'react';
import type { E2EInspection, InspectionStatus } from '../../lib/parityApi';

const STATUS_COLOR: Record<InspectionStatus, string> = {
  PASS: '#8fe0a7',
  FAIL: '#ff9c9c',
  PARTIAL: '#ffd47e',
  NOT_RUN: '#b6c1d0',
  NOT_CONFIGURED: '#b6c1d0',
  RESOURCE_LIMITED: '#ffb17e',
};

export function E2EFlowTimeline({ inspection }: { inspection: E2EInspection }) {
  return (
    <section aria-labelledby="e2e-inspection-title">
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          flexWrap: 'wrap',
          gap: 10,
          marginBottom: 14,
        }}
      >
        <h2 id="e2e-inspection-title" style={{ margin: 0, fontSize: 19 }}>
          Lifecycle inspection
        </h2>
        <span style={{ color: STATUS_COLOR[inspection.overall_status], fontWeight: 700 }}>
          {inspection.overall_status}
        </span>
        <span style={{ color: '#91a0b1', fontSize: 12 }}>
          {inspection.agent_name} · agent {inspection.agent_id}
        </span>
      </div>
      <div style={{ display: 'grid', gap: 4, marginBottom: 12, color: '#a8b3c1', fontSize: 12 }}>
        <div>
          Current published pointer: {inspection.published_version_number === null ? 'not verified' : `v${inspection.published_version_number} (${inspection.published_version_id})`}
        </div>
        {inspection.call_id && (
          <div>
            Call-pinned snapshot: {inspection.call_agent_version_number === null ? 'not verified' : `v${inspection.call_agent_version_number} (${inspection.call_agent_version_id || 'id unavailable'})`} · simulation: {inspection.is_simulation ? 'yes' : 'no'}
          </div>
        )}
      </div>

      <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 10 }}>
        {inspection.steps.map((step, index) => (
          <li
            key={step.key}
            style={{
              display: 'grid',
              gridTemplateColumns: '28px minmax(130px, 210px) minmax(90px, auto)',
              alignItems: 'start',
              gap: 10,
              padding: 13,
              background: '#111923',
              border: '1px solid #293544',
              borderRadius: 10,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                display: 'grid',
                placeItems: 'center',
                width: 24,
                height: 24,
                borderRadius: '50%',
                background: '#263548',
                color: '#e4eaf2',
                fontSize: 12,
                fontWeight: 700,
              }}
            >
              {index + 1}
            </span>
            <strong>{step.label}</strong>
            <span style={{ color: STATUS_COLOR[step.status], fontWeight: 700, fontSize: 12 }}>
              {step.status}
            </span>
            <p style={{ gridColumn: '2 / 4', margin: 0, color: '#b8c3d0', fontSize: 13, lineHeight: 1.5 }}>
              {step.detail}
            </p>
          </li>
        ))}
      </ol>

      <p style={{ color: '#a8b2bf', fontSize: 12, lineHeight: 1.5, marginBottom: 0 }}>
        No side effects were performed. The inspector reads tenant/environment-scoped records and never starts a carrier call or CRM writeback.
      </p>
    </section>
  );
}

export default E2EFlowTimeline;
