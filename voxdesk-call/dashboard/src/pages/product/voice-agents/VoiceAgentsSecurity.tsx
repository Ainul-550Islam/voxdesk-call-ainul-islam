import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
import type { SecurityItem } from '../../../types/home';

const DEFAULT_SECURITY_CONTROLS = [
  {
    id: 'recording',
    title: 'Call Recording & Consent Controls',
    description: 'Per-tenant recording policies, automatic recording disclaimers, dual-channel storage, and retention schedules.',
    badge: 'Recording & Consent',
    verified: true,
  },
  {
    id: 'redaction',
    title: 'Automatic PII & PCI Redaction',
    description: 'Scrub payment card numbers, SSNs, and sensitive credentials from transcripts and contact memory before persistence.',
    badge: 'Data Hygiene',
    verified: true,
  },
  {
    id: 'rbac',
    title: 'RBAC, JWT & Tenant Isolation',
    description: 'Row-level tenant isolation across all tables, short-lived JWT access tokens, HttpOnly refresh cookies, and 20+ granular permissions.',
    badge: 'Access Control',
    verified: true,
  },
  {
    id: 'audit',
    title: 'Immutable Audit Trail',
    description: 'Every agent publish, call takeover, transfer, and configuration change is logged with actor, IP, and timestamp.',
    badge: 'Auditability',
    verified: true,
  },
];

export function VoiceAgentsSecurity({ items }: { items?: SecurityItem[] }) {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
      <div className="max-w-3xl">
        <div className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
          Security & Governance
        </div>
        <h2 className="mt-2 text-3xl font-bold text-white sm:text-4xl">
          Security, Recording Consent & Tenant Isolation
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-white/60">
          Built-in call recording controls, PII redaction, role-based access control, and cryptographic webhook verification.
        </p>
      </div>

      <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {DEFAULT_SECURITY_CONTROLS.map((ctrl) => (
          <GlassCard key={ctrl.id} className="p-6">
            <div className="inline-flex rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-0.5 text-[10px] font-medium text-emerald-300">
              {ctrl.badge}
            </div>
            <h3 className="mt-4 text-sm font-semibold text-white">{ctrl.title}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/60">{ctrl.description}</p>
          </GlassCard>
        ))}
      </div>

      {items && items.length > 0 && (
        <div className="mt-8 grid gap-4 sm:grid-cols-3">
          {items.map((item) => (
            <div
              key={item.id}
              className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-xs"
            >
              <div className="font-semibold text-white">{item.title}</div>
              <div className="mt-1 text-white/60">{item.description}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default VoiceAgentsSecurity;
