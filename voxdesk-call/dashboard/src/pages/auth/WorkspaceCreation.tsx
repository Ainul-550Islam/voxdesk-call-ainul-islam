import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function WorkspaceCreation() {
  return (
    <GlassCard className="p-6">
      <div className="text-xs font-semibold uppercase tracking-wider text-blue-400">Automatic Provisioning</div>
      <h3 className="mt-2 text-base font-semibold text-white">What happens when you create a workspace</h3>
      <ul className="mt-4 space-y-2 text-xs text-white/65">
        <li>✓ Dedicated tenant_id with isolated Staging and Production environments</li>
        <li>✓ Pre-configured RBAC roles (Owner, Admin, Manager, Agent, Viewer)</li>
        <li>✓ Browser-based voice & chat simulator ready before binding phone numbers</li>
      </ul>
    </GlassCard>
  );
}
export default WorkspaceCreation;
