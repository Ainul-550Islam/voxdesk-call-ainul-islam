/**
 * dashboard/src/pages/product/customer-service/CustomerServiceHandoff.tsx
 * HUMAN HANDOFF CONTEXT — prop-driven panel.
 *
 * The component renders whatever `context` the caller passes: the authenticated
 * console passes a real handoff payload, and a marketing preview may pass
 * nothing. When nothing is supplied the component falls back to `SAMPLE_HANDOFF`
 * and says so out loud with a "Sample" badge — the previous revision defaulted
 * to a hard-coded fake context object with no disclosure, which made invented
 * customer data look like a live handoff.
 */
import React, { useMemo, useState } from 'react';

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
  /** Real handoff payload. When omitted, the labelled sample below is drawn. */
  context?: HandoffContext | null;
  strategy?: 'warm' | 'cold';
  onStrategyChange?: (strategy: 'warm' | 'cold') => void;
}

/**
 * Fixed sample context for the illustrative layout.
 *
 * Every field is fictional and static; the panel that renders it shows the
 * "Sample" badge so it can never be mistaken for a live customer's data.
 */
export const SAMPLE_HANDOFF: HandoffContext = {
  summary: 'Sample: caller asks about a delayed order and requests a human agent.',
  transcriptExcerpt: 'Customer: My order is late. AI: I can hand you to a specialist now.',
  crmData: {
    customerId: 'SAMPLE-0001',
    name: 'Sample Customer',
    tier: 'Sample tier',
    lastOrder: 'n/a (sample)',
    orderId: 'SAMPLE-ORDER',
    isSample: true,
  },
  sentiment: 'negative',
  intent: 'sample_intent',
  urgency: 'high',
  channel: 'chat',
  timestamp: '1970-01-01T00:00:00.000Z',
  customerId: 'SAMPLE-0001',
};

const URGENCY_STYLES: Record<HandoffContext['urgency'], string> = {
  high: 'bg-red-500/15 border-red-500/30 text-red-300',
  medium: 'bg-amber-500/15 border-amber-500/30 text-amber-300',
  low: 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300',
};

const SENTIMENT_STYLES: Record<HandoffContext['sentiment'], string> = {
  negative: 'bg-red-500/15 border-red-500/30 text-red-300',
  neutral: 'bg-white/10 border-white/20 text-white/70',
  positive: 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300',
};

export function CustomerServiceHandoff({ context, strategy: controlled, onStrategyChange }: Props) {
  const [internalStrategy, setInternalStrategy] = useState<'warm' | 'cold'>('warm');
  const strategy = controlled ?? internalStrategy;
  const setStrategy = (next: 'warm' | 'cold') => {
    if (controlled === undefined) setInternalStrategy(next);
    onStrategyChange?.(next);
  };

  const isSample = !context;
  const data = context ?? SAMPLE_HANDOFF;
  const crm = useMemo(() => data.crmData as Record<string, string | number | boolean | undefined>, [data.crmData]);
  const transcriptLines = useMemo(
    () => data.transcriptExcerpt.split(/\r?\n/).filter((line) => line.trim() !== ''),
    [data.transcriptExcerpt],
  );

  return (
    <div className="relative overflow-hidden rounded-[20px] border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] backdrop-blur-xl">
      <div className="relative p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h3 className="text-[14px] font-bold tracking-wide text-white uppercase">Human Handoff Context</h3>
            {isSample && (
              <span
                data-testid="handoff-sample-badge"
                className="rounded-full border border-amber-400/40 bg-amber-400/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200"
              >
                Sample
              </span>
            )}
          </div>
          <button className="text-white/40 hover:text-white/60 text-[16px] leading-none" aria-label="More">
            ⋯
          </button>
        </div>

        <div className="mt-5 rounded-[12px] border border-white/10 bg-black/30 p-4">
          <div className="text-[13px] font-medium text-white">Summary &amp; Handoff Trigger</div>
          <p className="mt-2 text-[12px] text-white/60">{data.summary}</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-white/50">Urgency:</span>
              <span className={`rounded-full border px-2.5 py-0.5 text-[12px] font-medium ${URGENCY_STYLES[data.urgency]}`}>
                {data.urgency}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-white/50">Sentiment:</span>
              <span className={`rounded-full border px-2.5 py-0.5 text-[12px] font-medium ${SENTIMENT_STYLES[data.sentiment]}`}>
                {data.sentiment}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-white/50">Intent:</span>
              <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[12px] text-white/70">
                {data.intent}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-white/50">Channel:</span>
              <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[12px] text-white/70">
                {data.channel}
              </span>
            </div>
          </div>
        </div>

        <div className="mt-4">
          <div className="text-[13px] font-medium text-white">Transcript Snippet</div>
          <div className="mt-2 space-y-1 rounded-[10px] border border-white/5 bg-black/20 p-3">
            {transcriptLines.length === 0 ? (
              <div className="text-[12px] text-white/40">No transcript excerpt supplied.</div>
            ) : (
              transcriptLines.map((line, index) => (
                <div key={index} className="text-[12px] text-white/60">
                  {line}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="mt-5">
          <div className="text-[13px] font-medium text-white">CRM Data</div>
          <div className="mt-2 flex flex-wrap items-center gap-2 rounded-[10px] border border-white/10 bg-black/30 px-3 py-2.5">
            <span className="text-[12px] text-white/50">ID:</span>
            <span className="text-[12px] font-medium text-white">{data.customerId}</span>
            {Object.entries(crm)
              .filter(([key]) => key !== 'customerId')
              .slice(0, 4)
              .map(([key, value]) => (
                <React.Fragment key={key}>
                  <span className="mx-1 h-4 w-px bg-white/10" />
                  <span className="text-[12px] text-white/70">
                    {key}: {String(value)}
                  </span>
                </React.Fragment>
              ))}
          </div>
        </div>

        <div className="mt-6">
          <div className="text-[13px] font-medium text-white">Handoff Strategy</div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div className={`rounded-[14px] border-2 p-4 transition-all ${strategy === 'warm' ? 'border-blue-500/50 bg-blue-500/5 shadow-[0_0_20px_rgba(59,130,246,0.15)]' : 'border-white/10 bg-white/[0.02]'}`}>
              <div className="flex items-start gap-2.5">
                <div className={`mt-0.5 h-7 w-7 rounded-[8px] flex items-center justify-center ${strategy === 'warm' ? 'bg-blue-500/20 text-blue-300' : 'bg-white/10 text-white/40'}`}>
                  <span className="text-[14px]">♡</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium text-white">Warm Handoff</div>
                  <div className="text-[11px] text-blue-400">(context preserved)</div>
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-[11px] text-white/60">
                  <span className="text-blue-400">📄</span> AI summary shared with the agent
                </div>
                <div className="flex items-center gap-2 text-[11px] text-white/60">
                  <span className="text-white/40">👤</span> Requires a configured transfer target
                </div>
              </div>
              <button
                onClick={() => setStrategy('warm')}
                className={`mt-4 w-full rounded-[10px] py-2 text-[12px] font-medium transition-colors ${strategy === 'warm' ? 'bg-blue-500 text-white hover:bg-blue-600' : 'bg-white/10 text-white/60 hover:bg-white/15'}`}
              >
                Select warm
              </button>
            </div>

            <div className={`rounded-[14px] border p-4 ${strategy === 'cold' ? 'border-white/20 bg-white/[0.04]' : 'border-white/10 bg-white/[0.02]'}`}>
              <div className="flex items-start gap-2.5">
                <div className="mt-0.5 h-7 w-7 rounded-[8px] bg-white/10 flex items-center justify-center text-white/40">
                  <span className="text-[14px]">❄</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium text-white">Cold Handoff</div>
                  <div className="text-[11px] text-white/40">queue only</div>
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-[11px] text-white/50">
                  <span>ⓘ</span> No live transfer
                </div>
                <div className="flex items-center gap-2 text-[11px] text-white/50">
                  <span>🕒</span> Wait time depends on the queue
                </div>
              </div>
              <button
                onClick={() => setStrategy('cold')}
                className="mt-4 w-full rounded-[10px] bg-white/10 py-2 text-[12px] font-medium text-white/50 hover:bg-white/15"
              >
                Select cold
              </button>
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-[10px] bg-black/40 border border-white/5 p-3 font-mono text-[10px] text-white/30">
          <div>Strategy: {strategy} — urgency: {data.urgency} — sentiment: {data.sentiment} — channel: {data.channel}</div>
          <div className="mt-1">Context Id: {data.customerId} — captured {data.timestamp}</div>
          <div className="mt-1 text-white/20">
            {isSample
              ? 'Sample data — no customer record was read and no handoff was performed.'
              : 'Handoff context supplied by the caller; transfer execution is a separate runtime path.'}
          </div>
        </div>
      </div>
    </div>
  );
}

export default CustomerServiceHandoff;

export function getHandoffStrategy(): 'warm' | 'cold' {
  return 'warm';
}
export function isHighUrgency(ctx: HandoffContext): boolean {
  return ctx.urgency === 'high';
}
export function isNegativeSentiment(ctx: HandoffContext): boolean {
  return ctx.sentiment === 'negative';
}
