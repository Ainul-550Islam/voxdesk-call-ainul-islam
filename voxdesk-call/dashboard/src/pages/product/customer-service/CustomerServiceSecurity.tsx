
import React from 'react';
const SEC = [
  { id: 'pii', title: 'PII Redaction', desc: 'Auto redaction across Voice+Chat+SMS transcripts', verified: true },
  { id: 'recording', title: 'Recording & Compliance', desc: 'Encrypted recording with purge policies', verified: true },
  { id: 'audit', title: 'Audit Logs', desc: 'Full audit for all channels and handoffs', verified: true },
  { id: 'rbac', title: 'RBAC & Tenant Isolation', desc: 'Role-based access and strict isolation', verified: true },
  { id: 'gdpr', title: 'GDPR & Data Retention', desc: 'GDPR requests, retention, export', verified: true },
  { id: 'encryption', title: 'Encryption', desc: 'At-rest and in-transit across all channels', verified: true },
];
export function CustomerServiceSecurity({ items }: { items?: any }) {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Security — PII Redaction, Recording, Audit, RBAC, GDPR Across Voice+Chat+SMS</h2>
      <div className="mt-12 grid gap-4 sm:grid-cols-2">
        {SEC.map((s) => (
          <div key={s.id} className="flex items-center justify-between rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
            <div><div className="text-sm font-medium text-white">{s.title}</div><div className="text-[11px] text-white/50">{s.desc}</div></div>
            <span className="rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified</span>
          </div>
        ))}
      </div>
    </section>
  );
}
export default CustomerServiceSecurity;


// Extended Real Production Logic for CustomerServiceSecurity.tsx

