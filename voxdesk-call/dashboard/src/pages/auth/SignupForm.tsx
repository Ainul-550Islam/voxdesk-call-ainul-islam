import React, { useState } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function SignupForm() {
  const [orgName, setOrgName] = useState('');
  const [email, setEmail] = useState('');
  const [industry, setIndustry] = useState('healthcare');
  const [created, setCreated] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!orgName || !email) return;
    setCreated(true);
  };

  return (
    <GlassCard className="p-6 sm:p-8">
      <h2 className="text-xl font-bold text-white">Create Your Tenant Workspace</h2>
      {created ? (
        <div className="mt-6 space-y-4">
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-4 text-xs text-emerald-300">
            Workspace <strong>{orgName}</strong> initialized with Staging & Production environments.
          </div>
          <a
            href="/dashboard/agents/new"
            className="inline-flex w-full items-center justify-center rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-black"
          >
            Launch Agent Builder →
          </a>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label htmlFor="signup-org" className="block text-xs font-medium text-white/75">Organization Name</label>
            <input
              id="signup-org"
              type="text"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              placeholder="Bright Smile Dental Group"
              className="input-glass mt-1.5"
            />
          </div>
          <div>
            <label htmlFor="signup-email" className="block text-xs font-medium text-white/75">Work Email</label>
            <input
              id="signup-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="founder@company.com"
              className="input-glass mt-1.5"
            />
          </div>
          <div>
            <label htmlFor="signup-industry" className="block text-xs font-medium text-white/75">Primary Industry</label>
            <select
              id="signup-industry"
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              className="input-glass mt-1.5 bg-black"
            >
              <option value="healthcare">Healthcare & Dental</option>
              <option value="home_services">Home Services & Dispatch</option>
              <option value="financial">Financial & Insurance</option>
              <option value="legal">Legal Intake</option>
              <option value="saas">Technology & SaaS</option>
            </select>
          </div>
          <button
            type="submit"
            className="w-full rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-black hover:bg-white/90"
          >
            Provision Workspace →
          </button>
        </form>
      )}
    </GlassCard>
  );
}
export default SignupForm;
