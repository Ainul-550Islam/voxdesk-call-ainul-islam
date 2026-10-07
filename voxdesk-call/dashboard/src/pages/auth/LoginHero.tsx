import React from 'react';

export function LoginHero() {
  return (
    <div>
      <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">
        Enterprise Authentication • JWT + HttpOnly Refresh
      </div>
      <h1 className="mt-4 text-3xl font-bold text-white sm:text-4xl">Sign in to VoxDesk</h1>
      <p className="mt-2 text-xs leading-relaxed text-white/60">
        Access your tenant-isolated Voice AI agents, live call supervisor console, and billing ledger.
      </p>
    </div>
  );
}
export default LoginHero;
