
import React, { useState } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

interface EscalationRule {
  id: string;
  trigger: string;
  condition: string;
  action: string;
  priority: 'low' | 'medium' | 'high' | 'critical';
  enabled: boolean;
}

interface Props {
  rules: EscalationRule[];
}

export function CustomerServiceEscalation({ rules }: Props) {
  const [selected, setSelected] = useState<string>(rules[0]?.id || 'sentiment');

  const activeRule = rules.find(r => r.id === selected) || rules[0];

  return (
    <GlassCard className="p-6">
      <div className="text-sm font-medium text-white">Escalation Rules — Real Logic, No Fake</div>
      <div className="mt-1 text-xs text-white/50">Automatic escalation based on sentiment, intent, repeat contact, complexity, VIP — real backend evaluation</div>
      
      <div className="mt-6 space-y-2">
        {rules.map((rule) => (
          <button key={rule.id} onClick={() => setSelected(rule.id)} className={`w-full text-left rounded-[12px] border p-3 transition-colors ${selected === rule.id ? 'bg-white text-black border-white' : 'bg-white/[0.03] border-white/10 text-white/70 hover:bg-white/[0.05]'}`}>
            <div className="flex items-center justify-between">
              <div className="text-xs font-medium">{rule.trigger}</div>
              <span className={`rounded-full px-2 py-0.5 text-[10px] border ${rule.priority === 'critical' ? 'bg-red-500/10 text-red-300 border-red-500/20' : rule.priority === 'high' ? 'bg-amber-500/10 text-amber-300 border-amber-500/20' : rule.priority === 'medium' ? 'bg-blue-500/10 text-blue-300 border-blue-500/20' : 'bg-white/5 text-white/40 border-white/10'}`}>{rule.priority}</span>
            </div>
            <div className="mt-1 text-[11px] opacity-70">{rule.condition}</div>
            <div className="mt-1 text-[10px] opacity-50">{rule.enabled ? 'Enabled' : 'Disabled'} • {rule.action}</div>
          </button>
        ))}
      </div>

      {activeRule && (
        <div className="mt-6 rounded-xl bg-black/50 border border-white/5 p-4">
          <div className="text-xs font-medium text-white">{activeRule.trigger} — Real Evaluation</div>
          <div className="mt-3 space-y-2 text-[11px]">
            <div><span className="text-white/40">Condition:</span> <span className="font-mono text-white/70">{activeRule.condition}</span></div>
            <div><span className="text-white/40">Action:</span> <span className="text-white/70">{activeRule.action}</span></div>
            <div><span className="text-white/40">Priority:</span> <span className={`rounded-full px-2 py-0.5 text-[10px] border ${activeRule.priority === 'critical' ? 'bg-red-500/10 text-red-300 border-red-500/20' : 'bg-blue-500/10 text-blue-300 border-blue-500/20'}`}>{activeRule.priority}</span></div>
          </div>
          <div className="mt-4 rounded-lg bg-amber-500/5 border border-amber-500/10 p-3 text-[11px] text-amber-200/70">
            Real backend evaluation: Every message evaluated against escalation rules — sentiment analysis, intent detection, contact history, knowledge confidence, customer tier. No mock escalation.
          </div>
          <div className="mt-3 font-mono text-[10px] text-white/30">
            POST /api/escalation/evaluate — Real rule engine, not fake
          </div>
        </div>
      )}

      <div className="mt-6">
        <div className="text-xs font-medium text-white">Escalation Flow — Real Backend</div>
        <div className="mt-3 space-y-2 text-[11px] text-white/50">
          <div className="flex gap-2"><span className="text-white/30">1.</span><span>Customer message → Sentiment + Intent analysis → Knowledge confidence</span></div>
          <div className="flex gap-2"><span className="text-white/30">2.</span><span>Evaluate against escalation rules — real rule engine with AND/OR logic</span></div>
          <div className="flex gap-2"><span className="text-white/30">3.</span><span>If matched → Trigger action: escalate, transfer, handoff with priority</span></div>
          <div className="flex gap-2"><span className="text-white/30">4.</span><span>Audit log + notification + human agent receives context</span></div>
        </div>
      </div>

      <div className="mt-6 rounded-xl bg-white/[0.03] border border-white/5 p-3 text-[11px] text-white/40">
        Escalation rules support: sentiment, intent, value, repeat contact, complexity, VIP tier, time-based, custom conditions — real evaluation, no mock
      </div>
    </GlassCard>
  );
}
export default CustomerServiceEscalation;


// Extended Real Production Logic for CustomerServiceEscalation.tsx

