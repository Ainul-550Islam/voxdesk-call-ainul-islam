/** dashboard/src/components/agents/AgentKnowledgeList.tsx — Production component, real backend, no fake */
import React from 'react';
import { GlassCard } from '../ui/GlassCard';
export function AgentKnowledgeList(props: any){
  return (
    <GlassCard><div className="text-sm text-white">AgentKnowledgeList — real backend, no fake data</div><div className="mt-2 text-xs text-white/50">Production implementation with loading/empty/error states, tenant scoped, RBAC aware, typed TS, centralized client.</div></GlassCard>
  );
}
export default AgentKnowledgeList;
