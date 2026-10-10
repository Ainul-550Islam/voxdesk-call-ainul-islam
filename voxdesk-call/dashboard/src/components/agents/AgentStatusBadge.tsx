import React from 'react';
import type { AgentStatus } from '../../types/agent';
const MAP: Record<AgentStatus,{ label:string; cls:string }> = { DRAFT: { label:'Draft', cls:'bg-white/10 text-white/70' }, VALIDATING: { label:'Validating', cls:'bg-amber-500/20 text-amber-300' }, VALID: { label:'Valid', cls:'bg-emerald-500/20 text-emerald-300' }, INVALID: { label:'Invalid', cls:'bg-red-500/20 text-red-300' }, PUBLISHED: { label:'Published', cls:'bg-blue-500/20 text-blue-300' }, UNPUBLISHED: { label:'Unpublished', cls:'bg-white/10 text-white/50' }, ARCHIVED: { label:'Archived', cls:'bg-white/5 text-white/30' }, RETIRED: { label:'Retired', cls:'bg-white/5 text-white/30' }, ERROR: { label:'Error', cls:'bg-red-500/20 text-red-300' } };
export function AgentStatusBadge({ status }: { status: AgentStatus }){ const m=MAP[status]||MAP.DRAFT; return <span className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-medium ${m.cls}`}>{m.label}</span>; }
export default AgentStatusBadge;
