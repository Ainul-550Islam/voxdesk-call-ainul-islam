/**
 * dashboard/src/app/app.tsx
 * SPA entry preserving Prompt1 Home + Prompt2 Agents + Prompt3 Use Cases.
 * Public: / , /home , /product/voice-agents , /use-cases , /use-cases/:slug , /solutions/use-cases
 * Authenticated: /dashboard/agents/* preserved.
 */
import React from 'react';
import { Providers } from './providers';
import { HomePage } from '../pages/home/HomePage';
import { VoiceAgentsPage } from '../pages/product/voice-agents/VoiceAgentsPage';
import { CustomerServicePage } from '../pages/product/customer-service/CustomerServicePage';
import { UseCasesPage } from '../pages/use-cases/UseCasesPage';
import { UseCasesDetailPage } from '../pages/use-cases/UseCasesDetailPage';
import { AgentsPage } from '../pages/agents/AgentsPage';
import { CreateAgentPage } from '../pages/agents/CreateAgentPage';
import { AgentBuilderPage } from '../pages/agents/AgentBuilderPage';
import { AgentSettingsPage } from '../pages/agents/AgentSettingsPage';
import { matchRoute, getSlugFromPath } from './router';
import '../styles/globals.css';
import '../styles/effects.css';
import '../styles/customer-service.css';
import '../styles/answering-service.css';
import '../styles/appointment-setter.css';
import '../styles/telemarketing.css';
import '../styles/industries.css';
import '../styles/integrations.css';
import '../styles/docs.css';
import '../styles/auth.css';
import '../styles/contact.css';
import '../styles/trust.css';

function getPath(): string {
  if (typeof window === 'undefined') return '/';
  return window.location.pathname;
}

export function App() {
  const path = getPath();
  const matched = matchRoute(path);
  const Component = matched?.component || HomePage;
  const isPublic = matched?.public ?? (path === '/' || path === '/home' || path.startsWith('/product') || path.startsWith('/use-cases') || path.startsWith('/solutions') || path.startsWith('/developers') || path.startsWith('/pricing'));
  const slug = getSlugFromPath(path);

  if (isPublic) {
    // Detail pages need slug prop
    if (matched?.pattern?.includes(':slug') && slug) {
      return (
        <Providers>
          <Component slug={slug} />
        </Providers>
      );
    }
    return (
      <Providers>
        <Component />
      </Providers>
    );
  }

  const isNewDashboardRoute = path.startsWith('/dashboard/agents');
  if (isNewDashboardRoute) {
    // Extract agentId if present
    const parts = path.split('/').filter(Boolean);
    const agentId = parts.length >= 3 ? parts[2] : undefined;
    if (matched?.pattern?.includes(':agentId') && agentId) {
      return (
        <Providers>
          <Component agentId={agentId} />
        </Providers>
      );
    }
    return (
      <Providers>
        <Component />
      </Providers>
    );
  }

  return (
    <Providers>
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold">Dashboard — Existing Logic Preserved</h1>
          <p className="mt-2 text-white/60">Route: {path} — handled by existing App.jsx hash routing</p>
          <div className="mt-6 flex gap-3 justify-center flex-wrap">
            <a href="/" className="rounded-xl bg-white px-5 py-2.5 text-sm font-medium text-black hover:bg-white/90">Go to Home</a>
            <a href="/product/voice-agents" className="rounded-xl border border-white/20 px-5 py-2.5 text-sm font-medium text-white hover:bg-white/10">Voice Agents</a>
            <a href="/use-cases" className="rounded-xl border border-white/20 px-5 py-2.5 text-sm font-medium text-white hover:bg-white/10">Use Cases</a>
            <a href="/dashboard/agents" className="rounded-xl border border-white/20 px-5 py-2.5 text-sm font-medium text-white hover:bg-white/10">My Agents</a>
          </div>
        </div>
      </div>
    </Providers>
  );
}

export default App;
