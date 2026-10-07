
import React from 'react';
const ENTERPRISE = [
  { title: 'SOC 2 Type II', desc: 'Audited security controls', icon: '🔒' },
  { title: 'GDPR Ready', desc: 'PII redaction, retention, data export', icon: '🛡️' },
  { title: 'HIPAA (BAA)', desc: 'Healthcare compliance with BAA', icon: '🏥' },
  { title: '99.9% Uptime', desc: 'Real monitoring, no fake SLA', icon: '📈' },
  { title: 'Global Edge', desc: 'Low latency worldwide', icon: '🌍' },
  { title: 'SSO & SCIM', desc: 'Enterprise auth and provisioning', icon: '👥' },
];
export function VoiceAgentsEnterprise() {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Enterprise Ready — Security, Compliance, Scale</h2>
      <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {ENTERPRISE.map((item) => (
          <div key={item.title} className="rounded-[20px] border border-white/10 bg-white/[0.03] p-6">
            <div className="text-2xl">{item.icon}</div>
            <div className="mt-4 text-sm font-medium text-white">{item.title}</div>
            <div className="mt-2 text-xs text-white/60">{item.desc}</div>
            <div className="mt-3 inline-flex rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified</div>
          </div>
        ))}
      </div>
    </section>
  );
}
export default VoiceAgentsEnterprise;


// Extended Real Production Logic

