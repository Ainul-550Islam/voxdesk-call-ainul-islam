import React from 'react';
import type { CapabilityItem } from '../../lib/parityApi';

const RETELL_PUBLIC_SURFACE: Record<string, string> = {
  public_site: 'Public voice-agent product, developer, integrations, pricing, and dashboard surfaces.',
  voice_agents: 'Build, configure, version, publish, and update voice agents through UI and API.',
  prompt_voice_model: 'Prompt, model, voice, speech, tools, dynamic variables, and agent settings.',
  chat: 'Chat agents, sessions, messages, and chat history.',
  sms: 'Two-way SMS with supported numbers/providers; outbound and in-call messaging.',
  knowledge: 'Knowledge sources, parsing, indexing, retrieval, and agent association.',
  tools_webhooks: 'Custom functions, integrations, webhooks, and external actions.',
  simulation_testing: 'Simulation, call testing, test cases, evaluation, and regression workflows.',
  telephony_phone_numbers: 'Phone numbers, SIP/custom telephony, inbound/outbound call paths.',
  call_transfer_dtmf: 'Transfers, DTMF/IVR navigation, and call control.',
  calls_transcripts_analysis: 'Call history, transcripts, recordings, post-call summaries and analysis.',
  live_monitoring_takeover: 'Live call monitoring, listen-in, whisper, and human takeover.',
  analytics_dashboards: 'Call/chat analytics, usage dashboards, and custom saved dashboards.',
  built_in_crm: 'Built-in CRM contact history and CRM context/outcome writeback.',
  calendar_integrations: 'Calendar integrations and booking/action tools.',
  campaigns_batch: 'Audience-based outbound campaigns, batch calls, scheduling, and retries.',
  workflows: 'Pre-call and post-call orchestration with integration actions.',
  conductor: 'Diagnosis, reproduction, simulation, proposed changes, and human approval.',
  guardrails_pii: 'Input/output guardrails and PII controls for stored content.',
  data_retention: 'Retention and data-storage controls for calls, chats, recordings, and logs.',
  billing_usage: 'Usage-based billing, invoices, credits, and concurrency/limits surfaces.',
  enterprise_security: 'Roles, API keys, SSO, and enterprise security/compliance controls.',
};

export function RetellParityMatrix({ capabilities }: { capabilities: CapabilityItem[] }) {
  return (
    <div style={{ overflowX: 'auto', border: '1px solid #293544', borderRadius: 12 }}>
      <table
        aria-label="Retell public capability comparison"
        style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820, background: '#111923' }}
      >
        <thead>
          <tr style={{ textAlign: 'left', background: '#172230' }}>
            <th scope="col" style={{ padding: 12 }}>Area</th>
            <th scope="col" style={{ padding: 12 }}>Retell public surface</th>
            <th scope="col" style={{ padding: 12 }}>Repository evidence</th>
            <th scope="col" style={{ padding: 12 }}>Status</th>
            <th scope="col" style={{ padding: 12 }}>Routes</th>
          </tr>
        </thead>
        <tbody>
          {capabilities.map((item) => (
            <tr key={item.key} style={{ borderTop: '1px solid #293544', verticalAlign: 'top' }}>
              <th scope="row" style={{ padding: 12, textAlign: 'left', whiteSpace: 'nowrap' }}>
                {item.label}
              </th>
              <td style={{ padding: 12, color: '#bbc5d3', maxWidth: 300 }}>
                {RETELL_PUBLIC_SURFACE[item.key] || 'See the documented public reference.'}
              </td>
              <td style={{ padding: 12, color: '#bbc5d3', maxWidth: 420 }}>{item.summary}</td>
              <td style={{ padding: 12, fontWeight: 700 }}>{item.status}</td>
              <td style={{ padding: 12, textAlign: 'center' }}>{item.evidence_routes.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default RetellParityMatrix;
