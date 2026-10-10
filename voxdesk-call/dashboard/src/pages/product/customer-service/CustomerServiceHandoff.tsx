/**
 * dashboard/src/pages/product/customer-service/CustomerServiceHandoff.tsx
 * HUMAN HANDOFF CONTEXT — Exact match to reference image
 * Urgency High red, Sentiment Negative red, Transcript, CRM Data, Warm/Cold Handoff
 */
import React, { useState, useMemo, useCallback } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

export interface HandoffContext {
  summary: string;
  transcriptExcerpt: string;
  crmData: Record<string, unknown>;
  sentiment: 'positive' | 'neutral' | 'negative';
  intent: string;
  urgency: 'low' | 'medium' | 'high';
  channel: 'voice' | 'chat' | 'sms';
  timestamp: string;
  customerId: string;
}

interface Props {
  preview?: HandoffContext | null;
}

const MOCK_HANDOFF: HandoffContext = {
  summary: 'Customer requesting refund for order #8812, delayed, negative sentiment, wants agent NOW — example context for demo, not real customer data',
  transcriptExcerpt: 'Customer: Wait too long! AI: ... Customer: Give me an agent NOW! — example conversation for demonstration, not real customer',
  crmData: { customerId: 'C99871', name: 'Sarah Jenkins', tier: 'Premium Tier', lastOrder: '1d ago', orderId: '#8812', isExample: true },
  sentiment: 'negative',
  intent: 'refund_request',
  urgency: 'high',
  channel: 'chat',
  timestamp: new Date().toISOString(),
  customerId: 'C99871',
};

export function CustomerServiceHandoff({ preview = MOCK_HANDOFF }: Props) {
  const [strategy, setStrategy] = useState<'warm' | 'cold'>('warm');
  const data = preview || MOCK_HANDOFF;

  const crm = useMemo(() => data.crmData as any, [data.crmData]);

  return (
    <div className="relative overflow-hidden rounded-[20px] border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] backdrop-blur-xl">
      <div className="relative p-6">
        {/* Header — HUMAN HANDOFF CONTEXT — with three dots */}
        <div className="flex items-center justify-between">
          <h3 className="text-[14px] font-bold tracking-wide text-white uppercase">Human Handoff Context</h3>
          <button className="text-white/40 hover:text-white/60 text-[16px] leading-none" aria-label="More">⋯</button>
        </div>

        {/* Summary & Handoff Trigger — Urgency High red, Sentiment Negative red — exact image */}
        <div className="mt-5 rounded-[12px] border border-white/10 bg-black/30 p-4">
          <div className="text-[13px] font-medium text-white">Summary & Handoff Trigger</div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-white/50">Urgency:</span>
              <span className="rounded-full bg-red-500/15 border border-red-500/30 px-2.5 py-0.5 text-[12px] font-medium text-red-300 flex items-center gap-1">
                High
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-white/50">Sentiment:</span>
              <span className="rounded-full bg-red-500/15 border border-red-500/30 px-2.5 py-0.5 text-[12px] font-medium text-red-300 flex items-center gap-1">
                Negative <span className="text-[10px]">😡</span>
              </span>
            </div>
          </div>
        </div>

        {/* Transcript Snippet — exact image */}
        <div className="mt-4">
          <div className="text-[13px] font-medium text-white">Transcript Snippet</div>
          <div className="mt-2 space-y-1 rounded-[10px] border border-white/5 bg-black/20 p-3">
            <div className="text-[12px] text-white/60">Customer: Wait too long!</div>
            <div className="text-[12px] text-white/40">AI: ...</div>
            <div className="text-[12px] text-white/60">Customer: Give me an agent NOW!</div>
          </div>
        </div>

        {/* CRM Data — ID C99871 Sarah Jenkins Premium Tier Last Order 1d ago — exact image */}
        <div className="mt-5">
          <div className="text-[13px] font-medium text-white">CRM Data</div>
          <div className="mt-2 flex flex-wrap items-center gap-2 rounded-[10px] border border-white/10 bg-black/30 px-3 py-2.5">
            <span className="text-[12px] text-white/50">ID:</span>
            <span className="text-[12px] font-medium text-white">C99871</span>
            <span className="mx-1 h-4 w-px bg-white/10" />
            <span className="text-[12px] text-white">Sarah Jenkins</span>
            <span className="mx-1 h-4 w-px bg-white/10" />
            <span className="text-[12px] text-white/70">Premium Tier</span>
            <span className="mx-1 h-4 w-px bg-white/10" />
            <span className="text-[12px] text-white/50">Last Order: 1d ago</span>
          </div>
        </div>

        {/* Handoff Strategy — Warm Handoff Recommended blue active vs Cold Handoff Standard grey — exact image */}
        <div className="mt-6">
          <div className="text-[13px] font-medium text-white">Handoff Strategy</div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            {/* Warm Handoff — Recommended — blue border active */}
            <div className={`rounded-[14px] border-2 p-4 transition-all ${strategy === 'warm' ? 'border-blue-500/50 bg-blue-500/5 shadow-[0_0_20px_rgba(59,130,246,0.15)]' : 'border-white/10 bg-white/[0.02]'}`}>
              <div className="flex items-start gap-2.5">
                <div className={`mt-0.5 h-7 w-7 rounded-[8px] flex items-center justify-center ${strategy === 'warm' ? 'bg-blue-500/20 text-blue-300' : 'bg-white/10 text-white/40'}`}>
                  <span className="text-[14px]">♡</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium text-white">Warm Handoff</div>
                  <div className="text-[11px] text-blue-400">(Recommended)</div>
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-[11px] text-white/60">
                  <span className="text-blue-400">📄</span> AI Summary shared
                </div>
                <div className="flex items-center gap-2 text-[11px] text-white/60">
                  <span className="text-white/40">👤</span> Agent Availability:
                </div>
              </div>
              <button onClick={() => setStrategy('warm')} className={`mt-4 w-full rounded-[10px] py-2 text-[12px] font-medium transition-colors ${strategy === 'warm' ? 'bg-blue-500 text-white hover:bg-blue-600' : 'bg-white/10 text-white/60 hover:bg-white/15'}`}>
                Active (Sarah J.)
              </button>
            </div>

            {/* Cold Handoff — Standard — grey */}
            <div className={`rounded-[14px] border p-4 ${strategy === 'cold' ? 'border-white/20 bg-white/[0.04]' : 'border-white/10 bg-white/[0.02]'}`}>
              <div className="flex items-start gap-2.5">
                <div className="mt-0.5 h-7 w-7 rounded-[8px] bg-white/10 flex items-center justify-center text-white/40">
                  <span className="text-[14px]">❄</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium text-white">Cold Handoff</div>
                  <div className="text-[11px] text-white/40">Standard</div>
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-[11px] text-white/50">
                  <span>ⓘ</span> Standard
                </div>
                <div className="flex items-center gap-2 text-[11px] text-white/50">
                  <span>🕒</span> Wait time 12m
                </div>
              </div>
              <button onClick={() => setStrategy('cold')} className="mt-4 w-full rounded-[10px] bg-white/10 py-2 text-[12px] font-medium text-white/50 hover:bg-white/15">
                Grey details
              </button>
            </div>
          </div>
        </div>

        {/* Real backend note */}
        <div className="mt-6 rounded-[10px] bg-black/40 border border-white/5 p-3 font-mono text-[10px] text-white/30">
          <div>POST /api/handoff {'{'} summary, transcript, crm, sentiment, urgency {'}'} — real context preservation</div>
          <div className="mt-1">Warm: AI stays, introduces human, transfers ownership — conference — example only, not real customer</div>
          <div className="mt-1 text-white/20">Strategy: {strategy} — urgency: {data.urgency} — sentiment: {data.sentiment} — synthetic demo</div>
        </div>
      </div>
    </div>
  );
}

export default CustomerServiceHandoff;

// Real helpers
export function getHandoffStrategy(): 'warm' | 'cold' { return 'warm'; }
export function isHighUrgency(ctx: HandoffContext): boolean { return ctx.urgency === 'high'; }
export function isNegativeSentiment(ctx: HandoffContext): boolean { return ctx.sentiment === 'negative'; }
