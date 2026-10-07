import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const PILLARS = [
  { title: 'Authentication & Session Rotation', desc: '15-minute access JWTs in memory + 14-day HttpOnly refresh cookies with single-use rotation.' },
  { title: 'Granular Role-Based Access Control', desc: '5 built-in roles (owner, admin, manager, agent, viewer) enforcing 20+ fine-grained permissions.' },
  { title: 'Tenant Data Isolation', desc: 'Every query is scoped by authenticated tenant_id and environment_id; client-supplied tenant IDs are rejected.' },
  { title: 'Automated PII & PCI Redaction', desc: 'Real-time scrubbing of credit card numbers, SSNs, and sensitive tokens before transcript persistence.' },
  { title: 'SSRF & Webhook Verification', desc: 'Outbound tool calls block RFC-1918/metadata IPs; inbound webhooks enforce HMAC-SHA256 replay windows.' },
  { title: 'Immutable Audit Logging', desc: 'Every login, role change, human call takeover, and agent publish is recorded with actor and IP.' },
];

export function SecurityCompliance() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 className="text-2xl font-bold text-white sm:text-3xl">Verified Security Controls</h2>
      <div className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {PILLARS.map((p) => (
          <GlassCard key={p.title} className="p-6">
            <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[10px] font-medium text-emerald-300">
              Enforced in Code
            </span>
            <h3 className="mt-3 text-base font-semibold text-white">{p.title}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/65">{p.desc}</p>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
export default SecurityCompliance;
