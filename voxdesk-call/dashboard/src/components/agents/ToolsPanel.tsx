import React, { useEffect, useState } from 'react';
import { getTools, attachTool, detachTool } from '../../api/agent-builder';
export function ToolsPanel({ agentId }: { agentId: string }){
  const [tools,setTools]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const load=async()=>{ setLoading(true); try{ const t=await getTools(agentId); setTools(t);} catch{} finally{ setLoading(false);} };
  useEffect(()=>{ load(); },[agentId]);
  return (
    <div className="space-y-4"><h2 className="text-sm font-medium text-white">Tools</h2><p className="text-xs text-white/50">Real registry, server-side validation, never browser URL execution.</p>
      {loading?<div className="h-20 animate-pulse rounded-xl bg-white/5" />:tools.length===0?<div className="rounded-xl border border-dashed border-white/10 p-8 text-center text-sm text-white/60">No tools attached — real registry empty, not inventing.</div>:<div className="space-y-2">{tools.map((tool:any)=>(<div key={tool.id} className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] p-3"><div><div className="text-sm text-white">{tool.name}</div><div className="text-xs text-white/40">{tool.description}</div></div><button onClick={async()=>{ await detachTool(agentId,tool.id); load(); }} className="text-xs text-red-300">Detach</button></div>))}</div>}
    </div>
  );
}
export default ToolsPanel;
