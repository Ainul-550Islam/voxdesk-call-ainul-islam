import React from 'react';
import { GlassCard } from '../ui/GlassCard';
import { AgentStatusBadge } from './AgentStatusBadge';
import type { Agent } from '../../types/agent';
export function AgentCard({ agent, onOpen }: { agent: Agent; onOpen: (id:string)=>void }){
  return (
    <GlassCard className="group hover:border-white/20 transition-all cursor-pointer" onClick={()=>onOpen(agent.id)}>
      <div className="flex items-start justify-between">
        <div><h3 className="text-sm font-medium text-white group-hover:text-white">{agent.name}</h3><p className="mt-1 text-xs text-white/50 line-clamp-2">{agent.description||'No description'}</p></div>
        <AgentStatusBadge status={agent.status as any} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {agent.voice && <span className="rounded-full bg-white/10 px-2.5 py-1 text-[10px] text-white/70">{agent.voice.name}</span>}
        {agent.language && <span className="rounded-full bg-white/10 px-2.5 py-1 text-[10px] text-white/70">{agent.language}</span>}
        {agent.model && <span className="rounded-full bg-white/10 px-2.5 py-1 text-[10px] text-white/70">{agent.model.name}</span>}
      </div>
      <div className="mt-4 flex items-center justify-between text-[11px] text-white/40">
        <span>Updated {new Date(agent.updated_at).toLocaleDateString()}</span>
        {typeof agent.calls_count==='number' && <span>{agent.calls_count} calls</span>}
      </div>
    </GlassCard>
  );
}
export default AgentCard;
