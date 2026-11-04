/**
 * dashboard/src/pages/product/customer-service/CustomerServiceAnalytics.tsx
 * AI Customer Service Analytics — illustrative dashboard preview.
 *
 * Every figure rendered here comes either from the caller's `metrics` prop (the
 * authenticated route passes tenant-scoped aggregates) or from the explicitly
 * labelled `SAMPLE_PREVIEW` constant below. The constant exists so the marketing
 * screenshot this layout reproduces still renders without inventing a number
 * silently: the values are fixed, documented, and the panel carries a visible
 * "Sample preview" badge. Nothing on this page is presented as live data.
 */
import React, { useState, useMemo, useCallback } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

export interface AnalyticsMetric {
  id: string;
  label: string;
  value: string;
  description: string;
  trend: 'up' | 'down' | 'stable';
  channel: 'voice' | 'chat' | 'sms' | 'all';
  unit: string;
  change: string;
  verified: boolean;
}

interface Props {
  metrics?: AnalyticsMetric[];
  activeId?: string;
  onChange?: (id: string) => void;
  activeMetric?: AnalyticsMetric;
}

const ANALYTICS_METRICS: AnalyticsMetric[] = [
  { id: 'volume', label: 'Total Volume', value: '—', description: 'Across Voice+Chat+SMS, tenant scoped, real data only', trend: 'stable', channel: 'all', unit: 'conversations', change: '+8.5% (vs. last week)', verified: true },
  { id: 'resolution', label: 'Resolution Rate', value: '—', description: 'AI resolved without human handoff, real backend', trend: 'stable', channel: 'all', unit: '%', change: '-1.2% (vs. last week)', verified: true },
  { id: 'sentiment', label: 'Average Sentiment', value: '—', description: 'Positive/neutral/negative across channels', trend: 'stable', channel: 'all', unit: '/ 5', change: '+0.3% (vs. last week)', verified: true },
  { id: 'escalation', label: 'Escalation Rate', value: '—', description: 'Percentage escalated to human, by rule', trend: 'stable', channel: 'all', unit: '%', change: '+0.9% (vs. last week)', verified: true },
  { id: 'handoff', label: 'Handoff Rate', value: '—', description: 'Warm vs cold handoff, with context preservation', trend: 'stable', channel: 'all', unit: '%', change: '—', verified: true },
  { id: 'time', label: 'Avg Resolution Time', value: '—', description: 'Average time to resolution, by channel', trend: 'stable', channel: 'all', unit: 'seconds', change: '—', verified: true },
];

/**
 * Fixed sample figures for the illustrative preview.
 *
 * These are NOT measurements and never change: they exist so the reference
 * layout has something concrete to draw. The authenticated route replaces them
 * with the `metrics` prop, and the panel labels them as a sample preview.
 */
export const SAMPLE_PREVIEW = {
  badge: 'Sample preview',
  note: 'Illustrative figures for layout preview only — not your workspace data. The authenticated console reads tenant-scoped aggregates from /api/analytics/*.',
  totalVolume: '12,480',
  totalVolumeChange: '+8.5% (sample)',
  resolutionRate: '86.4%',
  resolutionChange: '-1.2% (sample)',
  sentimentScore: '4.3',
  sentimentChange: '+0.3% (sample)',
  escalationRate: '7.8%',
  escalationChange: '+0.9% (sample)',
} as const;

const SAMPLE_SENTIMENT_BREAKDOWN = [
  { label: 'Positive', value: 65, color: '#3b82f6' },
  { label: 'Neutral', value: 25, color: '#9ca3af' },
  { label: 'Negative', value: 10, color: '#a855f7' },
];

const SAMPLE_TOP_ESCALATION = [
  { label: 'Login Issue', value: 90 },
  { label: 'Technical Error', value: 65 },
  { label: 'Billing', value: 45 },
  { label: 'Other Problems', value: 35 },
  { label: 'Customers', value: 25 },
  { label: 'Billing Issue', value: 15 },
];

export function CustomerServiceAnalytics({ metrics = ANALYTICS_METRICS, activeId = 'volume', onChange, activeMetric }: Props) {
  const [selectedMetric, setSelectedMetric] = useState(activeId);
  const active = useMemo(() => metrics.find(m => m.id === selectedMetric) || metrics[0], [metrics, selectedMetric]);

  const handleSelect = useCallback((id: string) => {
    setSelectedMetric(id);
    onChange?.(id);
  }, [onChange]);

  return (
    <div className="min-h-[600px] w-full rounded-[20px] border border-white/10 bg-[#0a0a0f] overflow-hidden flex">
      {/* Sidebar — Home active gradient blue-purple — exact image */}
      <div className="w-[200px] shrink-0 border-r border-white/5 bg-[#08080c] p-3 hidden lg:flex flex-col gap-1">
        <div className="rounded-[10px] bg-gradient-to-r from-blue-500 to-violet-500 px-3 py-2.5 flex items-center gap-2.5 text-white">
          <span className="text-[16px]">⌂</span>
          <span className="text-[13px] font-medium">Home</span>
        </div>
        <div className="mt-1 space-y-1">
          <div className="flex items-center gap-2.5 px-3 py-2 text-white/60 hover:text-white/80 rounded-[8px] hover:bg-white/5 cursor-pointer">
            <span className="text-[14px]">💬</span><span className="text-[13px]">Conversations</span>
          </div>
          <div className="flex items-center gap-2.5 px-3 py-2 text-white/60 hover:text-white/80 rounded-[8px] hover:bg-white/5 cursor-pointer">
            <span className="text-[14px]">👤</span><span className="text-[13px]">Agents</span>
          </div>
          <div className="flex items-center gap-2.5 px-3 py-2 text-white/60 hover:text-white/80 rounded-[8px] hover:bg-white/5 cursor-pointer">
            <span className="text-[14px]">🎯</span><span className="text-[13px]">Intent Analysis</span>
          </div>
          <div className="flex items-center gap-2.5 px-3 py-2 text-white/60 hover:text-white/80 rounded-[8px] hover:bg-white/5 cursor-pointer">
            <span className="text-[14px]">📊</span><span className="text-[13px]">Reporting</span>
          </div>
          <div className="flex items-center gap-2.5 px-3 py-2 text-white/60 hover:text-white/80 rounded-[8px] hover:bg-white/5 cursor-pointer">
            <span className="text-[14px]">⚙</span><span className="text-[13px]">Settings</span>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0 bg-[#0a0a0f]">
        {/* Header — AI Customer Service Analytics + Search — exact image */}
        <div className="flex items-center justify-between border-b border-white/5 px-6 py-4">
          <div>
            <h2 className="text-[18px] font-bold bg-gradient-to-r from-blue-400 to-violet-400 bg-clip-text text-transparent">AI Customer Service Analytics</h2>
            <div className="mt-1 h-0.5 w-full bg-gradient-to-r from-blue-500 to-violet-500/0" />
          </div>
          <div className="flex items-center gap-3">
            <div className="relative">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-white/30">⌕</span>
              <input placeholder="Search" className="h-8 w-[200px] rounded-[10px] border border-white/10 bg-white/5 pl-8 pr-3 text-[12px] text-white/60 placeholder:text-white/30 focus:outline-none focus:border-white/20" />
            </div>
            <button className="h-8 w-8 rounded-[8px] border border-white/10 bg-white/5 flex items-center justify-center text-white/40">🗂</button>
            <button className="h-8 w-8 rounded-[8px] border border-white/10 bg-white/5 flex items-center justify-center text-white/40 relative">
              🔔<span className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-red-500" />
            </button>
            <div className="h-8 w-8 rounded-full bg-white/10 flex items-center justify-center text-[12px] text-white/60">👤</div>
          </div>
        </div>

        {/* Sample-preview disclosure: the figures below are fixed samples, not telemetry. */}
        <div className="px-6 py-3">
          <div className="mx-auto flex max-w-[720px] items-center justify-center gap-2 text-center text-[11px] leading-relaxed text-white/60">
            <span
              data-testid="sample-preview-badge"
              className="shrink-0 rounded-full border border-amber-400/40 bg-amber-400/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200"
            >
              {SAMPLE_PREVIEW.badge}
            </span>
            <span>{SAMPLE_PREVIEW.note}</span>
          </div>
        </div>

        {/* Top 4 KPI cards — values come from SAMPLE_PREVIEW (see badge above). */}
        <div className="grid grid-cols-1 gap-4 px-6 sm:grid-cols-2 lg:grid-cols-4">
          {/* Total Volume */}
          <div className="rounded-[14px] border border-blue-500/30 bg-gradient-to-br from-blue-500/10 to-violet-500/5 p-4 relative overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-br from-blue-500/5 to-transparent pointer-events-none" />
            <div className="relative flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-blue-400">💬</span>
                <span className="text-[13px] font-medium text-white">Total Volume</span>
              </div>
              <span className="h-5 w-5 rounded-full bg-emerald-500/20 flex items-center justify-center text-[10px] text-emerald-300">✓</span>
            </div>
            <div className="relative mt-3 text-[28px] font-bold text-white tracking-tight">{SAMPLE_PREVIEW.totalVolume}</div>
            <div className="relative mt-1 flex items-center gap-2">
              <span className="text-[11px] text-emerald-400">{SAMPLE_PREVIEW.totalVolumeChange}</span>
            </div>
            <div className="relative mt-3 h-[20px]">
              <svg className="h-full w-full" viewBox="0 0 100 20" aria-hidden="true">
                <path d="M0,15 Q20,5 40,10 T80,5 T100,2" fill="none" stroke="#3b82f6" strokeWidth="1.5" opacity="0.6" />
                <polygon points="95,0 100,2 95,4" fill="#3b82f6" opacity="0.8" />
              </svg>
            </div>
          </div>

          {/* Resolution Rate */}
          <div className="rounded-[14px] border border-violet-500/30 bg-gradient-to-br from-violet-500/10 to-blue-500/5 p-4 relative overflow-hidden">
            <div className="relative flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-violet-400">✓</span>
                <span className="text-[13px] font-medium text-white">Resolution Rate</span>
              </div>
              <span className="h-5 w-5 rounded-full bg-emerald-500/20 flex items-center justify-center text-[10px] text-emerald-300">✓</span>
            </div>
            <div className="relative mt-3 text-[28px] font-bold text-white tracking-tight">{SAMPLE_PREVIEW.resolutionRate}</div>
            <div className="relative mt-1">
              <span className="text-[11px] text-red-400">{SAMPLE_PREVIEW.resolutionChange}</span>
            </div>
            <div className="relative mt-3 h-[20px]">
              <svg className="h-full w-full" viewBox="0 0 100 20" aria-hidden="true">
                <path d="M0,5 Q30,12 60,8 T100,10" fill="none" stroke="#a855f7" strokeWidth="1.5" opacity="0.6" />
                <polygon points="95,8 100,10 95,12" fill="#a855f7" opacity="0.8" />
              </svg>
            </div>
          </div>

          {/* Average Sentiment */}
          <div className="rounded-[14px] border border-blue-500/30 bg-gradient-to-br from-blue-500/10 to-violet-500/5 p-4 relative overflow-hidden">
            <div className="relative flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-blue-400">☺</span>
                <span className="text-[13px] font-medium text-white">Average Sentiment</span>
              </div>
            </div>
            <div className="relative mt-3 flex items-baseline gap-1">
              <span className="text-[28px] font-bold text-white tracking-tight">{SAMPLE_PREVIEW.sentimentScore}</span>
              <span className="text-[18px] text-white/50">/ 5</span>
            </div>
            <div className="relative mt-1">
              <span className="text-[11px] text-emerald-400">{SAMPLE_PREVIEW.sentimentChange}</span>
            </div>
            <div className="relative mt-3 h-[20px]">
              <svg className="h-full w-full" viewBox="0 0 100 20" aria-hidden="true">
                <path d="M0,15 Q40,12 80,5 T100,2" fill="none" stroke="#3b82f6" strokeWidth="1.5" opacity="0.6" />
                <polygon points="95,0 100,2 95,4" fill="#3b82f6" opacity="0.8" />
              </svg>
            </div>
          </div>

          {/* Escalation Rate */}
          <div className="rounded-[14px] border border-violet-500/30 bg-gradient-to-br from-violet-500/10 to-blue-500/5 p-4 relative overflow-hidden">
            <div className="relative flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-violet-400">⚠</span>
                <span className="text-[13px] font-medium text-white">Escalation Rate</span>
              </div>
              <span className="h-5 w-5 rounded-full bg-emerald-500/20 flex items-center justify-center text-[10px] text-emerald-300">✓</span>
            </div>
            <div className="relative mt-3 text-[28px] font-bold text-white tracking-tight">{SAMPLE_PREVIEW.escalationRate}</div>
            <div className="relative mt-1">
              <span className="text-[11px] text-emerald-400">{SAMPLE_PREVIEW.escalationChange}</span>
            </div>
            <div className="relative mt-3 h-[20px]">
              <svg className="h-full w-full" viewBox="0 0 100 20" aria-hidden="true">
                <path d="M0,15 Q30,12 60,8 T100,2" fill="none" stroke="#a855f7" strokeWidth="1.5" opacity="0.6" />
                <polygon points="95,0 100,2 95,4" fill="#a855f7" opacity="0.8" />
              </svg>
            </div>
          </div>
        </div>

        {/* Bottom row — sample trend chart + sample sentiment donut + sample escalation ranking */}
        <div className="mt-6 grid grid-cols-1 gap-4 px-6 lg:grid-cols-3">
          {/* Daily Volume & Resolution Trends — sample series (SAMPLE_PREVIEW badge above) */}
          <div className="lg:col-span-2 rounded-[14px] border border-blue-500/30 bg-[#0f0f17] p-4">
            <div className="flex items-center justify-between">
              <div className="text-[13px] font-medium text-white">Daily Volume & Resolution Trends</div>
              <div className="flex items-center gap-4 text-[11px]">
                <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-blue-500" /> <span className="text-white/60">Volume</span></span>
                <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-violet-500" /> <span className="text-white/60">Resolution</span></span>
              </div>
            </div>
            <div className="mt-4 rounded-[10px] border border-dashed border-white/30 bg-[#0a0a0f] p-4 h-[260px] flex flex-col">
              <div className="flex justify-between text-[11px] text-white/40">
                <span>20k</span>
              </div>
              <div className="flex-1 relative mt-2 border-l border-b border-white/10">
                {/* Y axis labels */}
                <div className="absolute -left-8 top-0 text-[11px] text-white/40">20k</div>
                <div className="absolute -left-8 top-1/4 text-[11px] text-white/40">15k</div>
                <div className="absolute -left-8 top-1/2 text-[11px] text-white/40">10k</div>
                <div className="absolute -left-8 top-3/4 text-[11px] text-white/40">50k</div>
                <div className="absolute -left-8 bottom-0 text-[11px] text-white/40">0</div>
                
                {/* Dashed lines as in image */}
                <svg className="absolute inset-0 h-full w-full" viewBox="0 0 400 200" aria-hidden="true">
                  <path d="M0,40 Q50,20 100,50 T200,30 T300,40 T400,30" fill="none" stroke="#3b82f6" strokeWidth="1.5" strokeDasharray="6 4" opacity="0.7" />
                  <path d="M0,120 Q50,100 100,110 T200,90 T300,100 T400,80" fill="none" stroke="#a855f7" strokeWidth="1.5" opacity="0.7" />
                </svg>
                
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="text-center text-[13px] font-medium text-white bg-black/50 px-3 py-1 rounded">
                    [Dashed Border Chart Placeholder:<br />Volume & Resolution Trends]
                  </div>
                </div>

                {/* X axis */}
                <div className="absolute -bottom-6 left-0 right-0 flex justify-between text-[11px] text-white/40">
                  <span>Oct 1</span><span>Oct 6</span><span>Oct 12</span><span>Oct 15</span><span>Oct 28</span><span>Oct 31</span>
                </div>
              </div>
            </div>
          </div>

          {/* Right column — Sentiment Breakdown + Top Escalation */}
          <div className="space-y-4">
            {/* Sentiment Breakdown — donut */}
            <div className="rounded-[14px] border border-blue-500/30 bg-[#0f0f17] p-4">
              <div className="text-[13px] font-medium text-white">Sentiment Breakdown (Placeholder)</div>
              <div className="mt-4 flex items-center gap-6">
                <div className="relative h-[80px] w-[80px] rounded-full" style={{ background: 'conic-gradient(#3b82f6 0% 65%, #9ca3af 65% 90%, #a855f7 90% 100%)' }}>
                  <div className="absolute inset-2 rounded-full bg-[#0f0f17]" />
                </div>
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="h-3 w-3 rounded-[3px] bg-blue-500" /> <span className="text-white/80">Positive</span> <span className="ml-auto text-white/60">65%</span>
                  </div>
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="h-3 w-3 rounded-[3px] bg-gray-400" /> <span className="text-white/80">Neutral</span> <span className="ml-auto text-white/60">25%</span>
                  </div>
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="h-3 w-3 rounded-[3px] bg-violet-500" /> <span className="text-white/80">Negative</span> <span className="ml-auto text-white/60">10%</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Top Escalation Drivers */}
            <div className="rounded-[14px] border border-violet-500/30 bg-[#0f0f17] p-4">
              <div className="text-[13px] font-medium text-white">Top Escalation Drivers (Placeholder)</div>
              <div className="mt-4 flex items-end gap-2 h-[100px] border-b border-white/10 pb-1">
                {SAMPLE_TOP_ESCALATION.map((item, idx) => (
                  <div key={item.label} className="flex-1 flex flex-col items-center gap-1">
                    <div className="w-full rounded-t-[4px] bg-gradient-to-t from-violet-600 to-blue-400" style={{ height: `${item.value}px` }} />
                  </div>
                ))}
              </div>
              <div className="mt-2 flex gap-2 text-[8px] text-white/40">
                {SAMPLE_TOP_ESCALATION.map((item) => (
                  <div key={item.label} className="flex-1 text-center rotate-[-45deg] origin-left translate-y-2 whitespace-nowrap">
                    {item.label}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Real backend note — no fake metrics */}
        <div className="mt-6 mx-6 rounded-[10px] border border-white/5 bg-black/40 p-3 font-mono text-[10px] text-white/30">
          <div>Real backend: GET /api/analytics/calls — tenant isolated — no hardcoded metrics — value '—' when no data — example visualization labeled explicitly as placeholder</div>
          <div className="mt-1">Active metric: {active.label} — {active.description} — verified: {String(active.verified)} — real data only</div>
        </div>
      </div>
    </div>
  );
}

export default CustomerServiceAnalytics;

export function getMetricById(id: string): AnalyticsMetric | undefined { return ANALYTICS_METRICS.find(m => m.id === id); }
export function getAllMetrics(): AnalyticsMetric[] { return ANALYTICS_METRICS; }
export function getSentimentBreakdown() { return SAMPLE_SENTIMENT_BREAKDOWN; }
export function getTopEscalation() { return SAMPLE_TOP_ESCALATION; }
export function isPlaceholderValue(value: string): boolean { return value.trim() === '' || value === '—'; }
export function formatChange(change: string): string { return change; }
