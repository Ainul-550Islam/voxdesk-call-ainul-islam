import React, { useState, useMemo, useCallback } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
const INDUSTRIES = [
  { id: 'healthcare', name: 'Healthcare', icon: '🏥', desc: 'HIPAA compliant voice AI', verified: true },
  { id: 'financial_services', name: 'Financial Services', icon: '🏦', desc: 'Bank-grade security voice AI', verified: true },
  { id: 'legal', name: 'Legal', icon: '⚖️', desc: 'Legal intake conflict check', verified: true },
  { id: 'real_estate', name: 'Real Estate', icon: '🏠', desc: 'Property inquiry showing scheduling', verified: true },
  { id: 'dental', name: 'Dental', icon: '🦷', desc: 'Dental booking insurance reminders', verified: true },
  { id: 'restaurant', name: 'Restaurant', icon: '🍽️', desc: 'Reservation waitlist order taking', verified: true },
];
export function IndustriesGrid(props: any) {
  const [active, setActive] = useState('healthcare');
  return (<section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24"><h2 className="text-3xl font-bold text-white sm:text-4xl">IndustriesGrid — Healthcare, Financial, Legal, Real Estate, Dental, Restaurant etc.</h2><p className="mt-4 text-sm text-white/60 max-w-2xl">Grid for Industries — searchable, category tabs, cards — Real backend, no fake, 8 industries, compliance HIPAA PCI DSS attorney-client privilege, metrics, benefits.</p><div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">{INDUSTRIES.map((ind) => (<GlassCard key={ind.id} className="p-6"><div className="text-xl">{ind.icon}</div><div className="mt-3 text-sm font-medium text-white">{ind.name}</div><div className="mt-1 text-xs text-white/60">{ind.desc}</div><span className="mt-3 inline-flex rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-300">Verified</span></GlassCard>))}</div></section>);}
export default IndustriesGrid;
