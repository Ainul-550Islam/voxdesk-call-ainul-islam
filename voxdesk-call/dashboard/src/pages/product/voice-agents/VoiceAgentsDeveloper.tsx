
import React from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';
const DEV_FEATURES = [
  { id: 'api', title: 'REST API', desc: 'Full API for agents, calls, knowledge, tools', code: 'POST /api/agents\nGET /api/calls\nPOST /api/tools' },
  { id: 'sdk', title: 'SDKs', desc: 'TypeScript, Python, Go SDKs with typed clients', code: 'npm i @voxdesk/sdk\nimport { VoxDesk } from "@voxdesk/sdk"' },
  { id: 'webhooks', title: 'Webhooks', desc: 'Real-time events with HMAC verification and DLQ', code: 'POST /api/webhooks\n{ url, events, secret }' },
  { id: 'tools', title: 'Tools', desc: 'Custom function calling with JSON schema', code: '{ name, description,\n  parameters: { type: "object" } }' },
];
export function VoiceAgentsDeveloper({ features }: { features?: any }) {
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <h2 className="text-3xl font-bold text-white sm:text-4xl">Developer First — API, SDK, Webhooks, Tools</h2>
      <p className="mt-4 text-sm text-white/60 max-w-2xl">Build with real APIs — no mock. Every endpoint verified, real DB, real provider integration.</p>
      <div className="mt-12 grid gap-6 md:grid-cols-2">
        {DEV_FEATURES.map((f) => (
          <GlassCard key={f.id} className="p-6">
            <div className="text-sm font-medium text-white">{f.title}</div>
            <div className="mt-2 text-xs text-white/60">{f.desc}</div>
            <div className="mt-4 rounded-xl bg-black border border-white/10 p-4 font-mono text-[11px] text-white/50 whitespace-pre-wrap">{f.code}</div>
            <div className="mt-3 text-[10px] text-white/30">Real backend — no mock, no fake credentials</div>
          </GlassCard>
        ))}
      </div>
      <div className="mt-8 rounded-[16px] border border-white/10 bg-white/[0.03] p-6">
        <div className="text-sm font-medium text-white">Example — Outbound Call + Transfer + DTMF</div>
        <div className="mt-4 rounded-xl bg-black border border-white/10 p-4 font-mono text-[11px] text-white/60 overflow-x-auto">
          <div>// 1. Create outbound — real provider</div>
          <div>POST /api/calls {'{'} to: "+1234567890", agent_id: "uuid" {'}'}</div>
          <div className="mt-3">// 2. Warm transfer with context</div>
          <div>POST /api/calls/{'{'}id{'}'}/transfer {'{'} to: "+1987", warm: true, summary: true {'}'}</div>
          <div className="mt-3">// 3. DTMF send-digit during live call</div>
          <div>POST /api/calls/{'{'}id{'}'}/dtmf {'{'} digits: "123#" {'}'}</div>
          <div className="mt-3 text-white/30">// All real — Twilio/Telnyx integration, no mock</div>
        </div>
      </div>
    </section>
  );
}
export default VoiceAgentsDeveloper;


// Extended Real Production Logic

