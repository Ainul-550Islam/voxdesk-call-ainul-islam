import React from 'react';
import type { BuilderSection } from '../../types/agent-builder';
const SECTIONS: { id: BuilderSection; label: string; icon: string }[] = [ { id:'overview', label:'Overview', icon:'◫' }, { id:'prompt', label:'Prompt', icon:'✎' }, { id:'voice', label:'Voice', icon:'🎙' }, { id:'model', label:'Model', icon:'◈' }, { id:'conversation', label:'Conversation', icon:'💬' }, { id:'knowledge', label:'Knowledge', icon:'📚' }, { id:'tools', label:'Tools', icon:'🔧' }, { id:'call_handling', label:'Call Handling', icon:'📞' }, { id:'security', label:'Security', icon:'🔒' }, { id:'versions', label:'Versions', icon:'🕘' }, { id:'test', label:'Test', icon:'▶' }, { id:'publish', label:'Publish', icon:'🚀' } ];
export function AgentBuilderSidebar({ active, onSelect }: { active: BuilderSection; onSelect: (s:BuilderSection)=>void }){
  return (
    <aside className="w-[260px] shrink-0 border-r border-white/10 bg-black/50 backdrop-blur" aria-label="Builder sections">
      <nav className="p-3 space-y-1">
        {SECTIONS.map(s=>(<button key={s.id} aria-current={active===s.id?'page':undefined} onClick={()=>onSelect(s.id)} className={`w-full flex items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition ${active===s.id?'bg-white text-black font-medium':'text-white/60 hover:text-white hover:bg-white/10'}`}><span className="text-base">{s.icon}</span><span>{s.label}</span></button>))}
      </nav>
    </aside>
  );
}
export default AgentBuilderSidebar;
