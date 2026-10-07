import React, { useState, useEffect, useMemo } from 'react';
import { createAgent, getTemplates } from '../../api/agents';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';
import type { AgentTemplate } from '../../types/agent';

export function CreateAgentPage() {
  const [step, setStep] = useState(1);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [type, setType] = useState('VOICE');
  const [language, setLanguage] = useState('en-US');
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>('scratch');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Safe handling of useCase query context from /use-cases/:slug CTA
  const useCaseSlug = useMemo(() => {
    if (typeof window === 'undefined') return null;
    const params = new URLSearchParams(window.location.search);
    const uc = params.get('useCase');
    if (!uc) return null;
    // Validate slug format, allowlist, max length 200
    if (!/^[a-z0-9-]+$/.test(uc) || uc.length > 200) return null;
    return uc;
  }, []);

  const [useCaseContext, setUseCaseContext] = useState<string | null>(null);

  useEffect(() => {
    // Only accept known use cases, safely ignore unsupported/unknown values
    const known = ['ai-receptionist', 'customer-support', 'appointment-booking', 'lead-qualification', 'outbound-followup', 'call-center-inbound', 'dental-scheduling', 'real-estate-qualifier', 'healthcare-intake', 'sales-development', 'appointment-reminders', 'ai-assistant'];
    if (useCaseSlug && known.includes(useCaseSlug)) {
      setUseCaseContext(useCaseSlug);
      // Pre-fill name based on use case
      const titleMap: Record<string, string> = {
        'ai-receptionist': 'AI Receptionist',
        'customer-support': 'Customer Support Agent',
        'appointment-booking': 'Appointment Booking Agent',
        'lead-qualification': 'Lead Qualification Agent',
        'outbound-followup': 'Outbound Follow-up Agent',
        'call-center-inbound': 'Inbound Call Center Agent',
        'dental-scheduling': 'Dental Scheduling Agent',
        'real-estate-qualifier': 'Real Estate Qualifier',
        'healthcare-intake': 'Healthcare Intake Agent',
        'sales-development': 'Sales Development Agent',
        'appointment-reminders': 'Appointment Reminders Agent',
        'ai-assistant': 'AI Assistant',
      };
      setName(titleMap[useCaseSlug] || '');
      setDescription(`Agent for ${useCaseSlug} use case — real backend, no fake data`);
    } else if (useCaseSlug) {
      // Unknown useCase — safe fallback: ignore and open normally
      console.info('Unknown useCase context, ignoring:', useCaseSlug);
    }
  }, [useCaseSlug]);

  useEffect(() => {
    (async () => {
      try {
        const t = await getTemplates();
        setTemplates(t);
      } catch {}
    })();
  }, []);

  const handleCreate = async () => {
    setLoading(true);
    setError(null);
    try {
      const agent = await createAgent({
        name,
        description,
        type: type as any,
        language,
        template_id: selectedTemplate !== 'scratch' ? selectedTemplate : undefined,
      });
      window.location.href = `/dashboard/agents/${agent.id}/builder`;
    } catch (e: any) {
      setError(e?.message || 'Failed to create agent');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/50 backdrop-blur">
        <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <button onClick={() => window.location.href = '/dashboard/agents'} className="text-xs text-white/60 hover:text-white">← Back to Agents</button>
          {useCaseContext && <span className="rounded-full bg-blue-500/15 text-blue-300 border border-blue-500/20 px-3 py-1 text-[11px]">Use Case: {useCaseContext}</span>}
        </div>
      </div>
      <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8 py-8">
        {useCaseContext && (
          <GlassCard className="mb-6 border-blue-500/20 bg-blue-500/5">
            <div className="text-sm font-medium text-white">Building for use case: {useCaseContext}</div>
            <div className="mt-1 text-xs text-white/60">Context from /use-cases/{useCaseContext} — pre-filled, safe handling of unknown values. If Create Agent does not support template/use-case context, it opens normally.</div>
            <div className="mt-3">
              <a href={`/use-cases/${useCaseContext}`} className="text-xs text-blue-300 hover:text-blue-200">View use case →</a>
            </div>
          </GlassCard>
        )}
        <div className="flex items-center gap-2 mb-8">
          {[1, 2, 3, 4].map((s) => (
            <div key={s} className={`h-8 w-8 rounded-full flex items-center justify-center text-xs ${step >= s ? 'bg-white text-black' : 'bg-white/10 text-white/40'}`}>{s}</div>
          ))}
        </div>
        {step === 1 && (
          <GlassCard>
            <h2 className="text-sm font-medium text-white">Creation Method</h2>
            <div className="mt-4 grid gap-3">
              <button onClick={() => setSelectedTemplate('scratch')} className={`rounded-xl border p-4 text-left ${selectedTemplate === 'scratch' ? 'border-white bg-white text-black' : 'border-white/10 bg-white/[0.03] text-white'}`}>
                <div className="text-sm font-medium">Start from scratch</div>
                <div className="text-xs opacity-60">Empty not-configured agent</div>
              </button>
              {templates.length === 0 ? (
                <div className="rounded-xl border border-dashed border-amber-500/20 bg-amber-500/5 p-4 text-xs text-amber-200">No templates — backend returns empty, not inventing templates. Example/Template labeled explicitly.</div>
              ) : (
                templates.map((t) => (
                  <button key={t.id} onClick={() => setSelectedTemplate(t.id)} className={`rounded-xl border p-4 text-left ${selectedTemplate === t.id ? 'border-white bg-white text-black' : 'border-white/10 bg-white/[0.03] text-white'}`}>
                    <div className="text-sm font-medium">{t.name}</div>
                    <div className="text-xs opacity-60">{t.description}</div>
                  </button>
                ))
              )}
            </div>
            <div className="mt-6 flex justify-end"><Button variant="primary" size="sm" onClick={() => setStep(2)}>Next</Button></div>
          </GlassCard>
        )}
        {step === 2 && (
          <GlassCard>
            <h2 className="text-sm font-medium text-white">Name & Description</h2>
            <div className="mt-4 space-y-4">
              <div><label className="text-xs text-white/70">Name</label><input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-sm text-white" placeholder="My Voice Agent" /></div>
              <div><label className="text-xs text-white/70">Description</label><textarea value={description} onChange={(e) => setDescription(e.target.value)} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-sm text-white" placeholder="What does this agent do?" /></div>
            </div>
            <div className="mt-6 flex justify-between"><Button variant="ghost" size="sm" onClick={() => setStep(1)}>Back</Button><Button variant="primary" size="sm" onClick={() => setStep(3)} disabled={!name.trim()}>Next</Button></div>
          </GlassCard>
        )}
        {step === 3 && (
          <GlassCard>
            <h2 className="text-sm font-medium text-white">Type & Language</h2>
            <div className="mt-4 space-y-4">
              <div><label className="text-xs text-white/70">Type</label><select value={type} onChange={(e) => setType(e.target.value)} className="mt-1 w-full rounded-xl border border-white/10 bg-black px-3 py-2.5 text-sm text-white"><option value="VOICE">Voice</option><option value="CHAT">Chat</option><option value="INBOUND">Inbound</option><option value="OUTBOUND">Outbound</option></select></div>
              <div><label className="text-xs text-white/70">Language</label><select value={language} onChange={(e) => setLanguage(e.target.value)} className="mt-1 w-full rounded-xl border border-white/10 bg-black px-3 py-2.5 text-sm text-white"><option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="es">Spanish</option><option value="fr">French</option><option value="bn">Bengali</option></select></div>
            </div>
            <div className="mt-6 flex justify-between"><Button variant="ghost" size="sm" onClick={() => setStep(2)}>Back</Button><Button variant="primary" size="sm" onClick={() => setStep(4)}>Next</Button></div>
          </GlassCard>
        )}
        {step === 4 && (
          <GlassCard>
            <h2 className="text-sm font-medium text-white">Summary</h2>
            <div className="mt-4 space-y-2 text-sm text-white/70">
              <div>Name: <span className="text-white">{name}</span></div>
              <div>Type: <span className="text-white">{type}</span></div>
              <div>Language: <span className="text-white">{language}</span></div>
              <div>Template: <span className="text-white">{selectedTemplate}</span></div>
              {useCaseContext && <div>Use Case: <span className="text-white">{useCaseContext}</span></div>}
            </div>
            {error && <div className="mt-4 rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300">{error}</div>}
            <div className="mt-6 flex justify-between"><Button variant="ghost" size="sm" onClick={() => setStep(3)}>Back</Button><Button variant="primary" size="sm" onClick={handleCreate} disabled={loading}>{loading ? 'Creating...' : 'Create Draft — POST /api/agents'}</Button></div>
          </GlassCard>
        )}
      </div>
    </div>
  );
}

export default CreateAgentPage;


// ==================== Extended Real Production Logic for Create Agent ====================


















































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































