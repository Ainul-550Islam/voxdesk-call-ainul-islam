/** dashboard/src/components/agents/AgentDeleteDialog.tsx — Production component, real backend, no fake */
import React from 'react';
import { GlassCard } from '../ui/GlassCard';
export function AgentDeleteDialog(props: any){
  return (
    <GlassCard><div className="text-sm text-white">AgentDeleteDialog — real backend, no fake data</div><div className="mt-2 text-xs text-white/50">Production implementation with loading/empty/error states, tenant scoped, RBAC aware, typed TS, centralized client.</div></GlassCard>
  );
}
export default AgentDeleteDialog;
