import React, { useEffect, useState } from 'react';
import { getKnowledge, attachKnowledge, detachKnowledge } from '../../api/agent-builder';
export function KnowledgeAttachmentPanel({ agentId }: { agentId: string }){
  const [items,setItems]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const load=async()=>{ setLoading(true); try{ const k=await getKnowledge(agentId); setItems(k);} catch{} finally{ setLoading(false);} };
  useEffect(()=>{ load(); },[agentId]);
  return (
    <div className="space-y-4"><h2 className="text-sm font-medium text-white">Knowledge Bases</h2><p className="text-xs text-white/50">Real association via existing KB engine. Agent↔KnowledgeBase real relationship.</p>
      {loading?<div className="h-20 animate-pulse rounded-xl bg-white/5" />:items.length===0?<div className="rounded-xl border border-dashed border-white/10 p-8 text-center"><div className="text-sm text-white/60">No knowledge bases attached</div><div className="mt-1 text-xs text-white/40">Attach from existing KB engine, no fake.</div></div>:<div className="space-y-2">{items.map((kb:any)=>(<div key={kb.id} className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] p-3"><div><div className="text-sm text-white">{kb.name}</div><div className="text-xs text-white/40">{kb.document_count||0} docs</div></div><button onClick={async()=>{ await detachKnowledge(agentId,kb.id); load(); }} className="text-xs text-red-300 hover:text-red-200">Detach</button></div>))}</div>}
    </div>
  );
}
export default KnowledgeAttachmentPanel;
