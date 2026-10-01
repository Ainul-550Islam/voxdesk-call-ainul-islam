import React from 'react';
import { HomePage } from '../pages/home/HomePage';
import { VoiceAgentsPage } from '../pages/product/voice-agents/VoiceAgentsPage';
import { AgentsPage } from '../pages/agents/AgentsPage';
import { CreateAgentPage } from '../pages/agents/CreateAgentPage';
import { AgentBuilderPage } from '../pages/agents/AgentBuilderPage';
import { AgentSettingsPage } from '../pages/agents/AgentSettingsPage';
import { UseCasesPage } from '../pages/use-cases/UseCasesPage';
import { UseCasesDetailPage } from '../pages/use-cases/UseCasesDetailPage';
import { CustomerServicePage } from '../pages/product/customer-service/CustomerServicePage';
import { AnsweringServicePage } from '../pages/product/answering-service/AnsweringServicePage';
import { AppointmentSetterPage } from '../pages/product/appointment-setter/AppointmentSetterPage';
import { TelemarketingPage } from '../pages/product/telemarketing/TelemarketingPage';
import { IndustriesPage } from '../pages/industries/IndustriesPage';
import { PricingPage } from '../pages/pricing/PricingPage';
import { DevelopersPage } from '../pages/developers/DevelopersPage';
import { ResourcesPage } from '../pages/resources/ResourcesPage';
import { BlogPage } from '../pages/blog/BlogPage';
import { SecurityPage } from '../pages/security/SecurityPage';
import { CompliancePage } from '../pages/compliance/CompliancePage';
import { AboutPage } from '../pages/company/AboutPage';
import { CareersPage } from '../pages/company/CareersPage';
import { CompanyContactPage } from '../pages/company/ContactPage';
import { PrivacyPage } from '../pages/legal/PrivacyPage';
import { TermsPage } from '../pages/legal/TermsPage';
import { OutboundPage } from '../pages/product/outbound/OutboundPage';
import { InboundPage } from '../pages/product/inbound/InboundPage';
import { VoiceCloningPage } from '../pages/product/voice-cloning/VoiceCloningPage';
import { AnalyticsPage } from '../pages/product/analytics/AnalyticsPage';
import { SolutionsPage } from '../pages/solutions/SolutionsPage';
import { TrustPage } from '../pages/trust/TrustPage';
import { ContactPage } from '../pages/contact/ContactPage';
import { BookDemoPage } from '../pages/contact/BookDemoPage';
import { LoginPage } from '../pages/auth/LoginPage';
import { SignupPage } from '../pages/auth/SignupPage';
import { DocsPage } from '../pages/docs/DocsPage';


import { IndustryDetailPage } from '../pages/industries/IndustryDetailPage';
import { IntegrationsPage } from '../pages/integrations/IntegrationsPage';
import { IntegrationDetailPage } from '../pages/integrations/IntegrationDetailPage';

export interface RouteConfig {
  path: string;
  component: React.ComponentType<any>;
  public?: boolean;
  title: string;
  pattern?: string;
}

export const routes: RouteConfig[] = [
  { path: '/', component: HomePage, public: true, title: 'VoxDesk — Enterprise Voice AI Infrastructure' },
  { path: '/home', component: HomePage, public: true, title: 'VoxDesk — Home' },
  { path: '/product/voice-agents', component: VoiceAgentsPage, public: true, title: 'VoxDesk — Voice Agents' },
  { path: '/product/customer-service', component: CustomerServicePage, public: true, title: 'AI Customer Service — Voice+Chat+SMS — VoxDesk' },
  { path: '/product/answering-service', component: AnsweringServicePage, public: true, title: 'AI Answering Service — 24/7 Answering, Booking, Routing, Custom Voice, CRM — VoxDesk' },
  { path: '/product/appointment-setter', component: AppointmentSetterPage, public: true, title: 'AI Appointment Setter — Calendar Booking, Reschedule/Cancel, Qualification, Reminders, Analytics — VoxDesk' },
  { path: '/product/telemarketing', component: TelemarketingPage, public: true, title: 'AI Telemarketing / Outbound — Campaigns, Lead Qualification, Follow-up, Scheduling, CRM Sync — VoxDesk' },
  { path: '/product/outbound', component: TelemarketingPage, public: true, title: 'AI Telemarketing / Outbound — Campaigns, Lead Qualification, Follow-up, Scheduling, CRM Sync — VoxDesk' },
  { path: '/industries', component: IndustriesPage, public: true, title: 'Industries — Healthcare, Financial Services, Legal, Real Estate, Dental, Restaurant — VoxDesk' },
  { path: '/industries/:slug', component: IndustryDetailPage, public: true, title: 'Industry Detail — Workflows, Integrations, Testimonials, Compliance — VoxDesk', pattern: '/industries/:slug' },
  { path: '/integrations', component: IntegrationsPage, public: true, title: 'Integrations — CRM, Telephony, Automation, Healthcare, Calendar, CX Tools — VoxDesk' },
  { path: '/integrations/:slug', component: IntegrationDetailPage, public: true, title: 'Integration Detail — Logo, Setup Steps, Workflow, Requirements, Example — VoxDesk', pattern: '/integrations/:slug' },

  { path: '/pricing', component: PricingPage, public: true, title: 'Pricing — Starter $99, Professional $299, Enterprise Custom — VoxDesk' },
  { path: '/developers', component: DevelopersPage, public: true, title: 'Developers — API Docs, SDKs, Webhooks — VoxDesk' },
  { path: '/resources', component: ResourcesPage, public: true, title: 'Resources — Blog, Docs, Guides — VoxDesk' },
  { path: '/blog', component: BlogPage, public: true, title: 'Blog — Voice AI Insights — VoxDesk' },
  { path: '/security', component: SecurityPage, public: true, title: 'Security — SOC2, Encryption, RBAC — VoxDesk' },
  { path: '/compliance', component: CompliancePage, public: true, title: 'Compliance — HIPAA, PCI DSS, GDPR, SOC2 — VoxDesk' },
  { path: '/company/about', component: AboutPage, public: true, title: 'About — Mission, Team — VoxDesk' },
  { path: '/company/careers', component: CareersPage, public: true, title: 'Careers — Join VoxDesk' },
  { path: '/company/contact', component: CompanyContactPage, public: true, title: 'Contact — Sales, Support — VoxDesk' },
  { path: '/legal/privacy', component: PrivacyPage, public: true, title: 'Privacy Policy — VoxDesk' },
  { path: '/legal/terms', component: TermsPage, public: true, title: 'Terms of Service — VoxDesk' },
  { path: '/product/outbound', component: OutboundPage, public: true, title: 'Outbound — Campaigns, Lead Qualification — VoxDesk' },
  { path: '/product/inbound', component: InboundPage, public: true, title: 'Inbound — Customer Service, Answering — VoxDesk' },
  { path: '/product/voice-cloning', component: VoiceCloningPage, public: true, title: 'Voice Cloning — Custom Voices — VoxDesk' },
  { path: '/product/analytics', component: AnalyticsPage, public: true, title: 'Analytics — Call Analytics, Insights — VoxDesk' },
  { path: '/solutions', component: SolutionsPage, public: true, title: 'Solutions — Industries, Use Cases, Workflows — VoxDesk' },
  { path: '/trust', component: TrustPage, public: true, title: 'Trust / Security — SOC2 HIPAA GDPR ISO Privacy Security Controls — VoxDesk' },
  { path: '/security/trust', component: TrustPage, public: true, title: 'Trust / Security — SOC2 HIPAA GDPR ISO — VoxDesk', pattern: '/security/trust' },
  { path: '/contact', component: ContactPage, public: true, title: 'Contact — Sales Form Qualification — VoxDesk' },
  { path: '/contact/book-demo', component: BookDemoPage, public: true, title: 'Book Demo — Calendar Booking Sales Qualification — VoxDesk' },
  { path: '/book-demo', component: BookDemoPage, public: true, title: 'Book Demo — Calendar Booking — VoxDesk' },
  { path: '/login', component: LoginPage, public: true, title: 'Login — Auth Workspace Creation — VoxDesk' },
  { path: '/signup', component: SignupPage, public: true, title: 'Signup — Auth Workspace Creation — VoxDesk' },
  { path: '/auth/login', component: LoginPage, public: true, title: 'Login — VoxDesk' },
  { path: '/auth/signup', component: SignupPage, public: true, title: 'Signup — VoxDesk' },
  { path: '/docs', component: DocsPage, public: true, title: 'Documentation — Build Test Deploy Data Monitor Reliability — VoxDesk' },
  { path: '/documentation', component: DocsPage, public: true, title: 'Documentation — Developer Docs API SDKs — VoxDesk' },
  { path: '/docs/:slug', component: DocsPage, public: true, title: 'Docs Detail — VoxDesk', pattern: '/docs/:slug' },

  { path: '/product', component: VoiceAgentsPage, public: true, title: 'VoxDesk — Voice Agents' },
  { path: '/use-cases', component: UseCasesPage, public: true, title: 'Use Cases — VoxDesk Voice AI' },
  { path: '/use-cases/:slug', component: UseCasesDetailPage, public: true, title: 'Use Case Detail — VoxDesk', pattern: '/use-cases/:slug' },
  { path: '/solutions/use-cases', component: UseCasesPage, public: true, title: 'Use Cases — VoxDesk', pattern: '/solutions/use-cases' },
  { path: '/solutions/use-cases/:slug', component: UseCasesDetailPage, public: true, title: 'Use Case Detail — VoxDesk', pattern: '/solutions/use-cases/:slug' },
  { path: '/dashboard/agents', component: AgentsPage, public: false, title: 'Voice Agents — Dashboard' },
  { path: '/dashboard/agents/new', component: CreateAgentPage, public: false, title: 'Create Agent — VoxDesk' },
  { path: '/dashboard/agents/:agentId/builder', component: AgentBuilderPage, public: false, title: 'Agent Builder — VoxDesk', pattern: '/dashboard/agents/:agentId/builder' },
  { path: '/dashboard/agents/:agentId/settings', component: AgentSettingsPage, public: false, title: 'Agent Settings — VoxDesk', pattern: '/dashboard/agents/:agentId/settings' },
  { path: '/dashboard/agents/:agentId', component: AgentBuilderPage, public: false, title: 'Agent Builder — VoxDesk', pattern: '/dashboard/agents/:agentId' },
];

function pathMatches(routePath: string, requestPath: string, pattern?: string): boolean {
  const check = pattern || routePath;
  if (check === requestPath) return true;
  const rp = check.split('/');
  const pp = requestPath.split('/');
  if (rp.length !== pp.length) return false;
  for (let i = 0; i < rp.length; i++) {
    if (rp[i].startsWith(':')) continue;
    if (rp[i] !== pp[i]) return false;
  }
  return true;
}

export function matchRoute(path: string): RouteConfig | null {
  const exact = routes.find((r) => r.path === path);
  if (exact) return exact;
  for (const r of routes) {
    if (pathMatches(r.path, path, r.pattern || (r.path.includes(':') ? r.path : undefined))) return r;
  }
  return routes.find((r) => r.path === '/') || null;
}

export function getSlugFromPath(path: string): string | null {
  const parts = path.split('/').filter(Boolean);
  if (parts.length >= 2 && (parts[0] === 'use-cases' || (parts[0] === 'solutions' && parts[1] === 'use-cases'))) {
    const last = parts[parts.length - 1];
    if (/^[a-z0-9-]+$/.test(last)) return last;
  }
  return null;
}
