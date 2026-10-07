import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { SectionHeader } from '../../components/ui/SectionHeader';
import type { HomeEvidenceStatus, SecurityItem } from '../../types/home';

function statusClasses(status: HomeEvidenceStatus): string {
  return status === 'PARTIAL'
    ? 'border-amber-500/20 bg-amber-500/10 text-amber-200'
    : 'border-white/15 bg-white/5 text-white/60';
}

export function HomeSecurity({ items = [] }: { items?: SecurityItem[] }) {
  return (
    <section className="bg-black px-4 py-20 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <SectionHeader
          badge="Security scope"
          title="Security controls require deployment-level verification"
          description="The entries below describe implementation areas and matching API paths. They are not security certifications, a complete penetration test, or a claim that every deployment is configured securely."
        />
        {items.length === 0 ? (
          <p className="mt-8 rounded-xl border border-white/10 p-5 text-sm text-white/60" role="status">
            No security-scope entries were returned by the public home API.
          </p>
        ) : (
          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((item) => (
              <GlassCard key={item.id}>
                <div className="flex items-start gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-sm text-white/60" aria-hidden="true">i</div>
                  <div>
                    <h3 className="text-sm font-semibold text-white">{item.title}</h3>
                    <p className="mt-2 text-xs leading-relaxed text-white/60">{item.description}</p>
                    <span className={`mt-3 inline-flex rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${statusClasses(item.status)}`}>
                      {item.status} · route evidence
                    </span>
                    {item.evidence_routes.length > 0 ? (
                      <ul className="mt-3 space-y-1 text-[10px] text-white/45" aria-label={`Registered route evidence for ${item.title}`}>
                        {item.evidence_routes.slice(0, 2).map((route) => <li key={route} className="break-all font-mono">{route}</li>)}
                      </ul>
                    ) : (
                      <p className="mt-3 text-[10px] text-white/45">No matching API route was returned by this deployment.</p>
                    )}
                  </div>
                </div>
              </GlassCard>
            ))}
          </div>
        )}
        <p className="mt-8 text-xs leading-relaxed text-white/40">
          No SOC 2, ISO 27001, HIPAA, GDPR, KMS, or deployment-wide row-level-security certification is asserted by this page.
        </p>
      </div>
    </section>
  );
}

export default HomeSecurity;
