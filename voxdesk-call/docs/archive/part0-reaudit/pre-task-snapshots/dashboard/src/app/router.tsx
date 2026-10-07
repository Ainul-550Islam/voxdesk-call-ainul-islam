/**
 * dashboard/src/app/router.tsx
 * Unified Enterprise Router for VoxDesk Business Website, Agent Studio & Operator Console
 * Includes Prompt 5 Public Website vs Authenticated App route boundary definitions.
 */
import React from 'react';
import { sanitizeReturnPath } from '../api/public-site';

export interface RouteConfig {
  path: string;
  title: string;
  description: string;
  component: string;
  legacyPath?: string;
  exact?: boolean;
  category:
    | 'public'
    | 'product'
    | 'solutions'
    | 'developers'
    | 'company'
    | 'legal'
    | 'auth'
    | 'dashboard';
}

export const ROUTES: RouteConfig[] = [
  // Home
  { path: '/', title: 'VoxDesk — Voice-agent workspace', description: 'Configure voice-agent workflows and explore the VoxDesk workspace. Provider availability depends on deployment configuration.', component: 'HomePage', exact: true, category: 'public' },
  { path: '/home', title: 'VoxDesk — Voice-agent workspace', description: 'Configure voice-agent workflows and explore the VoxDesk workspace. Provider availability depends on deployment configuration.', component: 'HomePage', exact: true, category: 'public' },

  // Product
  { path: '/product', title: 'Voice-agent workflows | VoxDesk', description: 'Explore illustrative voice-agent workflows and authenticated workspace surfaces.', component: 'VoiceAgentsPage', exact: true, category: 'product' },
  { path: '/product/voice-agents', title: 'Voice-agent workflows | VoxDesk', description: 'Explore agent creation, versioning, simulation, phone-number, and call-review surfaces. Live providers are deployment-specific.', component: 'VoiceAgentsPage', exact: true, category: 'product' },
  { path: '/product/customer-service', title: 'Customer-service workflow ideas | VoxDesk', description: 'Illustrative voice, chat, and messaging workflow patterns with deployment-specific provider requirements.', component: 'CustomerServicePage', exact: true, category: 'product' },
  { path: '/product/answering-service', title: 'Call-answering workflow ideas | VoxDesk', description: 'Illustrative call-answering patterns. Availability, routing, and external actions depend on deployment configuration.', component: 'AnsweringServicePage', exact: true, category: 'product' },
  { path: '/product/appointment-setter', title: 'Appointment workflow ideas | VoxDesk', description: 'Illustrative appointment workflows. Calendar access, booking, and reminders require separately verified integrations.', component: 'AppointmentSetterPage', exact: true, category: 'product' },
  { path: '/product/telemarketing', title: 'Outbound campaign operations | VoxDesk', description: 'Review persisted campaigns and explicit dry-run versus live-dialing boundaries.', component: 'TelemarketingPage', exact: true, category: 'product' },
  { path: '/product/outbound', title: 'Outbound calling workflow ideas | VoxDesk', description: 'Illustrative outbound patterns. Live dialing depends on consent, tenant controls, billing enforcement, and telephony configuration.', component: 'OutboundPage', exact: true, category: 'product' },
  { path: '/product/inbound', title: 'Inbound calling workflow ideas | VoxDesk', description: 'Illustrative inbound workflow steps; number, agent-version, webhook, and provider configuration must be verified.', component: 'InboundPage', exact: true, category: 'product' },
  { path: '/product/analytics', title: 'Analytics overview | VoxDesk', description: 'Learn about tenant-scoped analytics returned by the authenticated workspace. No sample metrics are displayed here.', component: 'AnalyticsPage', exact: true, category: 'product' },
  { path: '/product/voice-cloning', title: 'Voice configuration overview | VoxDesk', description: 'Review deployment-specific voice-provider configuration without implying a cloned voice or synthesis result.', component: 'VoiceCloningPage', exact: true, category: 'product' },

  // Solutions & Use Cases
  { path: '/solutions', title: 'Voice workflow patterns | VoxDesk', description: 'Explore illustrative product workflows and the configuration required for live operations.', component: 'SolutionsPage', exact: true, category: 'solutions' },
  { path: '/solutions/support', title: 'Customer support workflow example | VoxDesk', description: 'Illustrative customer-support voice and chat workflows; agent configuration, knowledge, and integrations must be verified for a deployment.', component: 'CustomerServicePage', exact: true, category: 'solutions' },
  { path: '/solutions/appointments', title: 'Appointment workflow example | VoxDesk', description: 'Illustrative appointment workflow patterns; calendar credentials, availability, and booking completion are not implied.', component: 'AppointmentSetterPage', exact: true, category: 'solutions' },
  { path: '/solutions/lead-qualification', title: 'Lead-qualification workflow example | VoxDesk', description: 'Illustrative lead-qualification workflow patterns; consent, telephony, and CRM state depend on tenant configuration.', component: 'TelemarketingPage', exact: true, category: 'solutions' },
  { path: '/solutions/outbound', title: 'Outbound workflow example | VoxDesk', description: 'Illustrative outbound workflow patterns; live dialing requires consent, tenant controls, billing enforcement, and configured telephony.', component: 'OutboundPage', exact: true, category: 'solutions' },

  { path: '/use-cases', title: 'Voice-agent use-case examples | VoxDesk', description: 'Browse public workflow examples. Catalog entries do not confirm tenant configuration or production outcomes.', component: 'UseCasesPage', exact: true, category: 'solutions' },
  { path: '/use-cases/:slug', title: 'Use-case example | VoxDesk', description: 'Illustrative catalog detail with workspace-specific availability left unverified.', component: 'UseCasesDetailPage', exact: false, category: 'solutions' },

  // Industries
  { path: '/industries', title: 'Illustrative sector examples | VoxDesk', description: 'Explore sector-level voice workflow ideas without claims of deployment, integration, outcome, or certification.', component: 'IndustriesPage', exact: true, category: 'solutions' },
  { path: '/industries/:slug', title: 'Sector workflow example | VoxDesk', description: 'Illustrative sector workflow notes; no regulated suitability or customer outcome is implied.', component: 'IndustryDetailPage', exact: false, category: 'solutions' },

  // Integrations
  { path: '/integrations', title: 'Integration adapter inventory | VoxDesk', description: 'Browse provider identifiers in the backend registry. Tenant configuration and provider health require authenticated verification.', component: 'IntegrationsPage', exact: true, category: 'product' },
  { path: '/integrations/:slug', title: 'Provider registry entry | VoxDesk', description: 'Repository-level provider information and the boundary between registered code and a tenant connection.', component: 'IntegrationDetailPage', exact: false, category: 'product' },

  // Pricing
  { path: '/pricing', title: 'VoxDesk plans and usage catalogue', description: 'View prices and allowances returned by the server-side billing catalogue, with seed-catalogue status disclosed.', component: 'PricingPage', exact: true, category: 'public' },

  // Developers & Docs
  { path: '/developers', title: 'Developer route inventory | VoxDesk', description: 'Read representative backend paths and access boundaries. No unverified SDK or universal webhook contract is advertised.', component: 'DevelopersPage', exact: true, category: 'developers' },
  { path: '/docs', title: 'Product documentation index | VoxDesk', description: 'Workspace guides and source-backed API notes. Generated OpenAPI documentation is not exposed in this build.', component: 'DocsPage', exact: true, category: 'developers' },
  { path: '/docs/:slug', title: 'Documentation section | VoxDesk', description: 'Workspace guide and product reference for the selected section.', component: 'DocsPage', exact: false, category: 'developers' },

  // Security, Trust & Compliance
  { path: '/security', title: 'Security implementation overview | VoxDesk', description: 'Review selected application controls and their implementation and deployment boundaries.', component: 'SecurityPage', exact: true, category: 'company' },
  { path: '/trust', title: 'Trust information | VoxDesk', description: 'Review what repository evidence can and cannot establish about a VoxDesk deployment.', component: 'TrustPage', exact: true, category: 'company' },
  { path: '/compliance', title: 'Compliance scope | VoxDesk', description: 'Application workflow controls do not constitute regulatory certification or deployment-specific legal advice.', component: 'CompliancePage', exact: true, category: 'company' },
  { path: '/status', title: 'Limited public component checks | VoxDesk', description: 'Point-in-time checks for selected API, database, widget-route, and signaling configuration surfaces; not an uptime or SLA monitor.', component: 'StatusPage', exact: true, category: 'company' },

  // Resources & Blog
  { path: '/resources', title: 'Product resources | VoxDesk', description: 'Browse currently published product references, API route notes, and illustrative workflow examples.', component: 'ResourcesPage', exact: true, category: 'public' },
  { path: '/blog', title: 'VoxDesk updates | VoxDesk', description: 'No verified engineering or product articles are currently published on this route.', component: 'BlogPage', exact: true, category: 'public' },
  { path: '/blog/:slug', title: 'Article availability | VoxDesk', description: 'Published article content is not generated from a URL slug; this route reports when no verified article exists.', component: 'BlogPost', exact: false, category: 'public' },

  // Company & Contact
  { path: '/about', title: 'About the VoxDesk product | VoxDesk', description: 'Product overview and deployment-specific capability boundaries; no unsupported customer, staff, certification, or uptime claims.', component: 'AboutPage', exact: true, category: 'company' },
  { path: '/careers', title: 'Career information | VoxDesk', description: 'No verified job openings are currently published; contact inquiries are not job applications.', component: 'CareersPage', exact: true, category: 'company' },
  { path: '/team', title: 'Team information availability | VoxDesk', description: 'No named leadership roster, biographies, or verified organizational chart is published on this route.', component: 'TeamPage', exact: true, category: 'company' },
  { path: '/contact', title: 'Contact VoxDesk | VoxDesk', description: 'Submit a persisted contact or sales inquiry. A submission is not a guaranteed response time or enterprise commitment.', component: 'ContactSalesPage', exact: true, category: 'company' },
  { path: '/contact-sales', title: 'Contact VoxDesk | VoxDesk', description: 'Submit a persisted contact or sales inquiry. A submission is not a guaranteed response time or enterprise commitment.', component: 'ContactSalesPage', exact: true, category: 'company' },
  { path: '/book-demo', title: 'Request information about a demo | VoxDesk', description: 'Send an inquiry to ask whether a configured VoxDesk demonstration is available; no live demo provider is implied.', component: 'BookDemoPage', exact: true, category: 'company' },


  // Auth
  { path: '/login', title: 'Sign In | VoxDesk', description: 'Sign in to your VoxDesk workspace.', component: 'LoginPage', exact: true, category: 'auth' },
  { path: '/signup', title: 'Create Workspace | VoxDesk', description: 'Create your tenant-isolated VoxDesk workspace.', component: 'SignupPage', exact: true, category: 'auth' },

  // Legal
  { path: '/privacy', title: 'Privacy information availability | VoxDesk', description: 'Request approved privacy and data-handling terms for the exact VoxDesk deployment.', component: 'PrivacyPage', exact: true, category: 'legal' },
  { path: '/terms', title: 'Terms availability | VoxDesk', description: 'Request current service and acceptable-use terms; this route does not publish a binding agreement.', component: 'TermsPage', exact: true, category: 'legal' },
  { path: '/dpa', title: 'Data processing agreement availability | VoxDesk', description: 'Request current contractual data-processing documents; this route is not a DPA.', component: 'DPA', exact: true, category: 'legal' },
  { path: '/sla', title: 'Service-level agreement availability | VoxDesk', description: 'Review the status of public service commitments and limited component checks.', component: 'SLA', exact: true, category: 'legal' },

  // Dashboard & Operator Console (Protected)
  { path: '/app', title: 'Operator Console | VoxDesk', description: 'Authenticated multi-tenant workspace.', component: 'OperatorConsole', exact: true, category: 'dashboard' },
  { path: '/app/overview', title: 'Workspace Overview | VoxDesk', description: 'Authenticated workspace overview.', component: 'OperatorConsole', exact: true, category: 'dashboard' },
  { path: '/app/agents', title: 'Voice & Chat Agents | VoxDesk Studio', description: 'Create, test, version, and publish AI agents.', component: 'AgentsPage', exact: true, category: 'dashboard' },
  { path: '/app/public-keys', title: 'Public Widget Keys | VoxDesk Studio', description: 'Manage scoped public widget keys and allowed origins.', component: 'WidgetSettings', exact: true, category: 'dashboard' },
  { path: '/dashboard', title: 'Agent Studio | VoxDesk', description: 'Manage and deploy voice and chat agents.', component: 'AgentsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/agents', title: 'Voice & Chat Agents | VoxDesk Studio', description: 'Create, test, version, and publish AI agents.', component: 'AgentsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/agents/new', title: 'Create Agent | VoxDesk Studio', description: 'Build a new voice or chat agent.', component: 'CreateAgentPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/agents/:id', title: 'Agent Detail & Versions | VoxDesk Studio', description: 'Inspect durable agent state, versions, and readiness.', component: 'AgentDetailPage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/builder', title: 'Agent Builder | VoxDesk Studio', description: 'Configure agent prompt, voice, knowledge, and tools.', component: 'AgentBuilderPage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/versions/:version', title: 'Agent Version Snapshot | VoxDesk Studio', description: 'Inspect immutable agent version snapshot and diffs.', component: 'AgentVersionDetailPage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/settings', title: 'Agent Settings | VoxDesk Studio', description: 'Configure webhook, telephony, public widget keys, and compliance settings.', component: 'AgentSettingsPage', exact: false, category: 'dashboard' },
  // Dense operator table over the same durable agent list.
  { path: '/dashboard/agents/list', title: 'All Agents | VoxDesk Studio', description: 'Search, filter, and sort every tenant-scoped agent.', component: 'AgentListPage', exact: true, category: 'dashboard' },
  { path: '/app/agents/list', title: 'All Agents | VoxDesk Studio', description: 'Search, filter, and sort every tenant-scoped agent.', component: 'AgentListPage', exact: true, category: 'dashboard' },
  // Knowledge base: upload, ingest URLs, reindex, and preview retrieval.
  { path: '/dashboard/knowledge', title: 'Knowledge Base | VoxDesk Studio', description: 'Upload documents, ingest URLs, reindex, and preview retrieval.', component: 'AgentKnowledgePage', exact: true, category: 'dashboard' },
  { path: '/app/knowledge', title: 'Knowledge Base | VoxDesk Studio', description: 'Upload documents, ingest URLs, reindex, and preview retrieval.', component: 'AgentKnowledgePage', exact: true, category: 'dashboard' },
  // Archived agents (exact path, so it wins over `/dashboard/agents/:id`).
  { path: '/dashboard/agents/archive', title: 'Archived Agents | VoxDesk Studio', description: 'Review archived agents and restore them to draft.', component: 'AgentArchivePage', exact: true, category: 'dashboard' },
  { path: '/app/agents/archive', title: 'Archived Agents | VoxDesk Studio', description: 'Review archived agents and restore them to draft.', component: 'AgentArchivePage', exact: true, category: 'dashboard' },
  // Per-agent configuration surfaces.
  { path: '/dashboard/agents/:id/voice', title: 'Voice Configuration | VoxDesk Studio', description: 'Choose a TTS provider and voice from the providers this deployment can actually use.', component: 'AgentVoicePage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/model', title: 'Model Configuration | VoxDesk Studio', description: 'Choose an LLM provider and runtime model preset, and tune sampling.', component: 'AgentModelPage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/tools', title: 'Agent Tools | VoxDesk Studio', description: 'Register, enable, and disable custom functions for this agent.', component: 'AgentToolsPage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/test-history', title: 'Test History | VoxDesk Studio', description: 'Persisted simulation runs and a live browser test session for this agent.', component: 'AgentTestHistoryPage', exact: false, category: 'dashboard' },
  { path: '/dashboard/agents/:id/duplicate', title: 'Duplicate Agent | VoxDesk Studio', description: 'Clone an existing agent into a new draft.', component: 'AgentDuplicatePage', exact: false, category: 'dashboard' },
  { path: '/dashboard/public-keys', title: 'Public Widget Keys & Web Widget | VoxDesk Studio', description: 'Manage scoped public keys, allowed origins, and web widget embeds.', component: 'WidgetSettings', exact: true, category: 'dashboard' },
  { path: '/dashboard/chat-agents', title: 'Chat Agents & Sessions | VoxDesk Studio', description: 'Durable Chat Agents, immutable versions, and Contact Memory chat sessions.', component: 'ChatAgentsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/contacts', title: 'Contacts & Contact Memory | VoxDesk Studio', description: 'E.164 tenant contacts and durable Contact Memory key/value facts.', component: 'ContactsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/playground', title: 'Agent Playground | VoxDesk Studio', description: 'LLM prompt and multi-turn simulation playground pinned to immutable AgentVersions.', component: 'AgentPlaygroundPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/simulations', title: 'Simulations & Batch Suites | VoxDesk Studio', description: 'Durable TestSuites, version-pinned TestCases, batch runner, and WebRTC/PSTN call testers.', component: 'SimulationsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/qa-scorecards', title: 'QA Scorecards & Evaluations | VoxDesk Studio', description: 'Deterministic & LLM-judge evaluation rules, evidence-backed QA scorecards, and version comparisons.', component: 'QAScorecardsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/conductor', title: 'Conductor AI Control Plane | VoxDesk Studio', description: 'Permission-scoped AI copilot for building, reviewing, simulating, and safely applying immutable AgentVersion changes.', component: 'ConductorPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/phone-numbers', title: 'Phone Numbers & SIP Trunking | VoxDesk Studio', description: 'Provision E.164 phone numbers, bind inbound/outbound agents, and verify SIP trunks.', component: 'PhoneNumbersPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/call-runtime', title: 'Voice Call Runtime & Control | VoxDesk Studio', description: 'Originate outbound calls, monitor real-time media sessions, send DTMF, and execute transfers.', component: 'CallRuntimePage', exact: true, category: 'dashboard' },
  { path: '/app/phone-numbers', title: 'Phone Numbers & SIP Trunking | VoxDesk Studio', description: 'Provision E.164 phone numbers, bind inbound/outbound agents, and verify SIP trunks.', component: 'PhoneNumbersPage', exact: true, category: 'dashboard' },
  { path: '/app/call-runtime', title: 'Voice Call Runtime & Control | VoxDesk Studio', description: 'Originate outbound calls, monitor real-time media sessions, send DTMF, and execute transfers.', component: 'CallRuntimePage', exact: true, category: 'dashboard' },
  { path: '/studio/conductor', title: 'Conductor AI Control Plane | VoxDesk Studio', description: 'Permission-scoped AI copilot for building, reviewing, simulating, and safely applying immutable AgentVersion changes.', component: 'ConductorPage', exact: true, category: 'dashboard' },
  { path: '/product/playground', title: 'Agent Playground | VoxDesk Studio', description: 'LLM prompt and multi-turn simulation playground pinned to immutable AgentVersions.', component: 'AgentPlaygroundPage', exact: true, category: 'product' },
  { path: '/product/simulations', title: 'Simulations & Batch Suites | VoxDesk Studio', description: 'Durable TestSuites, version-pinned TestCases, batch runner, and WebRTC/PSTN call testers.', component: 'SimulationsPage', exact: true, category: 'product' },
  { path: '/product/qa-scorecards', title: 'QA Scorecards & Evaluations | VoxDesk Studio', description: 'Deterministic & LLM-judge evaluation rules, evidence-backed QA scorecards, and version comparisons.', component: 'QAScorecardsPage', exact: true, category: 'product' },
  { path: '/product/conductor', title: 'Conductor AI Control Plane | VoxDesk Studio', description: 'Permission-scoped AI copilot for building, reviewing, simulating, and safely applying immutable AgentVersion changes.', component: 'ConductorPage', exact: true, category: 'product' },
  { path: '/dashboard/analytics', title: 'Analytics | VoxDesk Studio', description: 'Open the authenticated, tenant-scoped legacy analytics console.', component: 'AnalyticsPage', legacyPath: '/analytics', exact: true, category: 'dashboard' },
  { path: '/dashboard/final-parity', title: 'Final Retell Parity | VoxDesk Studio', description: 'Evidence-backed Retell public-surface comparison, integration state, and read-only lifecycle inspection.', component: 'FinalParityPage', exact: true, category: 'dashboard' },
  { path: '/workflows', title: 'Workflows | VoxDesk Studio', description: 'Review, publish, and execute tenant-scoped workflow definitions.', component: 'WorkflowsPage', exact: true, category: 'dashboard' },
  { path: '/dashboard/workflows', title: 'Workflows | VoxDesk Studio', description: 'Review, publish, and execute tenant-scoped workflow definitions.', component: 'WorkflowsPage', exact: true, category: 'dashboard' },
  { path: '/campaigns', title: 'Campaigns | VoxDesk Studio', description: 'Manage tenant-scoped outbound campaign audiences and schedules.', component: 'LegacyCampaignsPage', legacyPath: '/campaigns', exact: true, category: 'dashboard' },
  { path: '/dashboard/campaigns', title: 'Campaigns | VoxDesk Studio', description: 'Manage tenant-scoped outbound campaign audiences and schedules.', component: 'LegacyCampaignsPage', legacyPath: '/campaigns', exact: true, category: 'dashboard' },
  { path: '/settings', title: 'Security & Settings | VoxDesk Studio', description: 'Manage workspace security, sessions, API keys, and identity policy.', component: 'LegacySettingsPage', legacyPath: '/security-settings', exact: true, category: 'dashboard' },
  { path: '/dashboard/settings', title: 'Security & Settings | VoxDesk Studio', description: 'Manage workspace security, sessions, API keys, and identity policy.', component: 'LegacySettingsPage', legacyPath: '/security-settings', exact: true, category: 'dashboard' },
  { path: '/security-settings', title: 'Security & Settings | VoxDesk Studio', description: 'Manage workspace security, sessions, API keys, and identity policy.', component: 'LegacySettingsPage', legacyPath: '/security-settings', exact: true, category: 'dashboard' },
  { path: '/billing', title: 'Billing | VoxDesk Studio', description: 'View plan, usage, invoices, and authorized billing actions.', component: 'LegacyBillingPage', legacyPath: '/billing', exact: true, category: 'dashboard' },
  { path: '/dashboard/billing', title: 'Billing | VoxDesk Studio', description: 'View plan, usage, invoices, and authorized billing actions.', component: 'LegacyBillingPage', legacyPath: '/billing', exact: true, category: 'dashboard' },
  { path: '/workspace', title: 'Operator Console | VoxDesk', description: 'Multi-tenant operator console for calls, leads, billing, and audit.', component: 'OperatorConsole', exact: false, category: 'dashboard' },
  { path: '/calls/:id', title: 'Call Details | VoxDesk', description: 'Inspect a persisted tenant-scoped call and transcript.', component: 'CallLogConsole', exact: false, category: 'dashboard' },
  { path: '/calls', title: 'Live Call Monitoring & Logs | VoxDesk', description: 'Inspect tenant-scoped call sessions and transcripts.', component: 'CallLogConsole', exact: true, category: 'dashboard' },
  { path: '/dashboard/calls', title: 'Live Call Monitoring & Logs | VoxDesk', description: 'Inspect tenant-scoped call sessions and transcripts.', component: 'CallLogConsole', exact: true, category: 'dashboard' },
  { path: '/dashboard/calls/:id', title: 'Call Details | VoxDesk', description: 'Inspect a persisted tenant-scoped call and transcript.', component: 'CallLogConsole', exact: false, category: 'dashboard' },
  { path: '/agent', title: 'Agent Configuration | VoxDesk', description: 'Configure your voice agent.', component: 'AgentsPage', exact: true, category: 'dashboard' },
  { path: '/phone-numbers', title: 'Phone Numbers & SIP Trunking | VoxDesk', description: 'Provision E.164 numbers and SIP trunks.', component: 'PhoneNumbersPage', exact: true, category: 'dashboard' },
];

const PROTECTED_PATH_PREFIXES = [
  '/app',
  '/dashboard',
  '/studio',
  '/workspace',
  '/calls',
  '/billing',
  '/settings',
  '/governance',
  '/workflows',
];

export function isAuthPath(pathname: string): boolean {
  const clean = pathname.split('?')[0].replace(/\/+$/, '') || '/';
  return clean === '/login' || clean === '/signup';
}

export function isProtectedPath(pathname: string): boolean {
  const clean = pathname.split('?')[0].replace(/\/+$/, '') || '/';
  if (PROTECTED_PATH_PREFIXES.some((p) => clean === p || clean.startsWith(`${p}/`))) {
    return true;
  }
  const matched = matchRoute(clean);
  return matched?.route.category === 'dashboard';
}

export function isPublicPath(pathname: string): boolean {
  return !isAuthPath(pathname) && !isProtectedPath(pathname);
}

export function resolveRouteBoundary(
  pathname: string,
  isAuthenticated: boolean,
  requestedNext?: string | null,
): {
  allowed: boolean;
  category: 'public' | 'auth' | 'protected';
  redirectTo: string | null;
} {
  if (isAuthPath(pathname)) {
    if (isAuthenticated) {
      return {
        allowed: true,
        category: 'auth',
        redirectTo: sanitizeReturnPath(requestedNext, '/app/overview'),
      };
    }
    return { allowed: true, category: 'auth', redirectTo: null };
  }

  if (isProtectedPath(pathname)) {
    if (!isAuthenticated) {
      const safeTarget = sanitizeReturnPath(pathname, '/app/overview');
      return {
        allowed: false,
        category: 'protected',
        redirectTo: `/login?next=${encodeURIComponent(safeTarget)}`,
      };
    }
    return { allowed: true, category: 'protected', redirectTo: null };
  }

  return { allowed: true, category: 'public', redirectTo: null };
}

export function matchRoute(
  pathname: string,
): { route: RouteConfig; params: Record<string, string> } | null {
  const clean = pathname.split('?')[0].replace(/\/+$/, '') || '/';

  for (const r of ROUTES) {
    if (r.exact && r.path === clean) {
      return { route: r, params: {} };
    }
  }

  for (const r of ROUTES) {
    if (!r.exact && r.path.includes(':')) {
      const routeParts = r.path.split('/');
      const pathParts = clean.split('/');
      if (routeParts.length === pathParts.length) {
        const params: Record<string, string> = {};
        let match = true;
        for (let i = 0; i < routeParts.length; i++) {
          if (routeParts[i].startsWith(':')) {
            params[routeParts[i].slice(1)] = decodeURIComponent(pathParts[i]);
          } else if (routeParts[i] !== pathParts[i]) {
            match = false;
            break;
          }
        }
        if (match) return { route: r, params };
      }
    }
  }

  return null;
}

export default ROUTES;
