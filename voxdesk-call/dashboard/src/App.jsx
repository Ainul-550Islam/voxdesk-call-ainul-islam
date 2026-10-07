/**
 * The application root.
 *
 * STEP 8 phase 1: this file used to *be* the product — header, stat tiles,
 * call table and a transcript panel, all inline, on one screen. The Shell,
 * router and seven pages already existed in the tree but nothing imported
 * them, so none of it ran. This composes them.
 *
 * Responsibilities kept deliberately small:
 *
 *   1. own the session (`bootstrap`, `logout`, the 401 handler),
 *   2. build one `can()` from the server's permission list,
 *   3. resolve the current hash route to a page,
 *   4. refuse to render a page the account has no permission for.
 *
 * Everything else belongs to a page. The audit's F5 finding was that fetch
 * logic lived here; it now lives in `useApi`, and this file makes no data
 * request of its own beyond `/auth/me`.
 */
import './styles/industries.css';
import './styles/integrations.css';
import './styles/pricing.css';
import './styles/developers.css';
import './styles/resources.css';
import './styles/blog.css';
import './styles/security.css';
import './styles/compliance.css';
import './styles/company.css';
import './styles/legal.css';
import './styles/solutions.css';
import './styles/use-cases.css';
import './styles/trust.css';
import './styles/contact.css';
import './styles/auth.css';
import './styles/docs.css';
import { useCallback, useEffect, useMemo, useState } from 'react'

import Login from './components/Login'
import Shell from './components/Shell'
import { EmptyState } from './components/ui'
import { bootstrap, logout, setUnauthorizedHandler } from './lib/api'
import { makeCan, PERMISSIONS as P } from './lib/permissions'
import { match, navigate, useRoute } from './lib/router'

import Agent from './pages/Agent'
import Analytics from './pages/Analytics'
import Appointments from './pages/Appointments'
import CallDetail from './pages/CallDetail'
import Calls from './pages/Calls'
import Campaigns from './pages/Campaigns'
import Billing from './pages/Billing'
import Audit from './pages/Audit'
import EnterpriseSecuritySettings from './pages/security/EnterpriseSecuritySettings'
import Team from './pages/Team'
import Integrations from './pages/Integrations'
import Knowledge from './pages/Knowledge'
import Leads from './pages/Leads'
import Overview from './pages/Overview'
import AgentsPage from './pages/agents/AgentsPage'
import CreateAgentPage from './pages/agents/CreateAgentPage'
import AgentBuilderPage from './pages/agents/AgentBuilderPage'
import AgentSettingsPage from './pages/agents/AgentSettingsPage'
import VoiceAgentsPage from './pages/product/voice-agents/VoiceAgentsPage'
import UseCasesPage from './pages/use-cases/UseCasesPage'
import UseCasesDetailPage from './pages/use-cases/UseCasesDetailPage'
import IndustriesPage from './pages/industries/IndustriesPage';
import IndustryDetailPage from './pages/industries/IndustryDetailPage';
import IntegrationsPage from './pages/integrations/IntegrationsPage';
import IntegrationDetailPage from './pages/integrations/IntegrationDetailPage';
import PricingPage from './pages/pricing/PricingPage';
import DevelopersPage from './pages/developers/DevelopersPage';
import ResourcesPage from './pages/resources/ResourcesPage';
import BlogPage from './pages/blog/BlogPage';
import SecurityPage from './pages/security/SecurityPage';
import CompliancePage from './pages/compliance/CompliancePage';
import AboutPage from './pages/company/AboutPage';
import CareersPage from './pages/company/CareersPage';
import CompanyContactPage from './pages/company/ContactPage';
import PrivacyPage from './pages/legal/PrivacyPage';
import TermsPage from './pages/legal/TermsPage';
import OutboundPage from './pages/product/outbound/OutboundPage';
import InboundPage from './pages/product/inbound/InboundPage';
import VoiceCloningPage from './pages/product/voice-cloning/VoiceCloningPage';
import AnalyticsPage from './pages/product/analytics/AnalyticsPage';
import SolutionsPage from './pages/solutions/SolutionsPage';
import TrustPage from './pages/trust/TrustPage';
import ContactPage from './pages/contact/ContactPage';
import BookDemoPage from './pages/contact/BookDemoPage';
import LoginPage from './pages/auth/LoginPage';
import SignupPage from './pages/auth/SignupPage';
import DocsPage from './pages/docs/DocsPage';



/**
 * The route table.
 *
 * `permission` is the *client* half of the gate and is UX only — it decides
 * whether we render the page or an explanatory panel, and it is what keeps a
 * manager from staring at a bare 403. The server enforces the same permission
 * again on every request the page makes, and that is the boundary that
 * matters. Phase 2 adds these one page at a time;
 */
const ROUTES = [
  {
    pattern: '/',
    title: 'Overview',
    permission: P.ANALYTICS_READ,
    render: (props) => <Overview {...props} />,
  },
  {
    pattern: '/overview',
    title: 'Overview',
    permission: P.ANALYTICS_READ,
    render: (props) => <Overview {...props} />,
  },
  {
    pattern: '/calls',
    title: 'Calls',
    permission: P.CALL_READ,
    render: (props) => <Calls {...props} />,
  },
  {
    // Before `/calls` would match it — `match()` is exact on segment count,
    // so ordering is not load-bearing, but keeping the specific route first
    // survives someone later making the matcher prefix-based.
    pattern: '/calls/:id',
    title: 'Call detail',
    permission: P.CALL_READ,
    render: (props, params) => <CallDetail {...props} callId={params.id} />,
  },
  {
    pattern: '/leads',
    title: 'Leads',
    permission: P.LEAD_READ,
    render: (props) => <Leads {...props} />,
  },
  {
    pattern: '/appointments',
    title: 'Appointments',
    permission: P.APPOINTMENT_READ,
    render: (props) => <Appointments {...props} />,
  },
  {
    pattern: '/campaigns',
    title: 'Campaigns',
    permission: P.CAMPAIGN_READ,
    render: (props) => <Campaigns {...props} />,
  },
  {
    pattern: '/analytics',
    title: 'Analytics',
    permission: P.ANALYTICS_READ,
    render: (props) => <Analytics {...props} />,
  },
  {
    // Read access is `tenant:read`, which every role has -- a viewer may see
    // how the agent is configured. The write controls inside the page are
    // gated separately on `tenant:update`, and the server enforces that again
    // on `PATCH .../voice`.
    pattern: '/agent',
    title: 'Agent',
    permission: P.TENANT_READ,
    render: (props) => <Agent {...props} />,
  },
  {
    // Read is `knowledge:read`, which every role has. Upload, reindex and
    // archive are gated on `knowledge:write` inside the page, and a hard
    // purge additionally on `knowledge:delete` -- the server re-checks all
    // three.
    pattern: '/knowledge',
    title: 'Knowledge',
    permission: P.KNOWLEDGE_READ,
    render: (props) => <Knowledge {...props} />,
  },
  {
    // Read covers both the CRM and calendar catalogues and the tenant's
    // configured rows. Connect, disable and remove are gated on
    // `integration:write` inside the page; the server re-checks. Note the
    // backend gates "test connection" on read, not write -- a diagnosis
    // changes nothing.
    pattern: '/integrations',
    title: 'Integrations',
    permission: P.INTEGRATION_READ,
    render: (props) => <Integrations {...props} />,
  },
  {
    // `billing:read` is owner+admin. Every mutation is gated on
    // `billing:write`, which is owner-only, inside the page -- and the
    // server re-checks.
    pattern: '/billing',
    title: 'Billing',
    permission: P.BILLING_READ,
    render: (props) => <Billing {...props} />,
  },
  {
    // `user:read` is owner+admin only -- manager and below get the
    // access-denied state. Create, role change and deactivate are gated
    // separately inside the page, and the server re-checks all of them.
    pattern: '/team',
    title: 'Team',
    permission: P.USER_READ,
    render: (props) => <Team {...props} />,
  },
  {
    // `audit:read` is owner+admin, same as the team page. View-only: the
    // endpoint offers no mutation, and neither does this route.
    pattern: '/audit',
    title: 'Audit log',
    permission: P.AUDIT_READ,
    render: (props) => <Audit {...props} />,
  },
  {
    // Session management is available to every signed-in user. Tenant policy,
    // role inventory, and API-key controls remain permission-gated inside the
    // page and enforced again by the corresponding backend endpoints.
    pattern: '/security-settings',
    title: 'Security & sessions',
    permission: P.TENANT_READ,
    render: (props) => <EnterpriseSecuritySettings {...props} />,
  },
  {
    // PUBLIC product page also accessible via hash for legacy dashboard entry — real backend data, no fake.
    pattern: '/product/voice-agents',
    title: 'Voice Agents',
    permission: P.TENANT_READ,
    render: () => <VoiceAgentsPage />,
  },
  {
    pattern: '/agents',
    title: 'Voice Agents',
    permission: P.TENANT_READ,
    render: () => <AgentsPage />,
  },
  {
    pattern: '/dashboard/agents',
    title: 'Voice Agents',
    permission: P.TENANT_READ,
    render: () => <AgentsPage />,
  },
  {
    pattern: '/agents/new',
    title: 'Create Agent',
    permission: P.TENANT_READ,
    render: () => <CreateAgentPage />,
  },
  {
    pattern: '/dashboard/agents/new',
    title: 'Create Agent',
    permission: P.TENANT_READ,
    render: () => <CreateAgentPage />,
  },
  {
    pattern: '/agents/:id/builder',
    title: 'Agent Builder',
    permission: P.TENANT_READ,
    render: (props, params) => <AgentBuilderPage agentId={params.id} {...props} />,
  },
  {
    pattern: '/dashboard/agents/:id/builder',
    title: 'Agent Builder',
    permission: P.TENANT_READ,
    render: (props, params) => <AgentBuilderPage agentId={params.id} {...props} />,
  },
  {
    pattern: '/agents/:id/settings',
    title: 'Agent Settings',
    permission: P.TENANT_READ,
    render: (props, params) => <AgentSettingsPage agentId={params.id} {...props} />,
  },
  {
    pattern: '/dashboard/agents/:id/settings',
    title: 'Agent Settings',
    permission: P.TENANT_READ,
    render: (props, params) => <AgentSettingsPage agentId={params.id} {...props} />,
  },
  {
    pattern: '/agents/:id',
    title: 'Agent Builder',
    permission: P.TENANT_READ,
    render: (props, params) => <AgentBuilderPage agentId={params.id} {...props} />,
  },
  {
    pattern: '/dashboard/agents/:id',
    title: 'Agent Builder',
    permission: P.TENANT_READ,
    render: (props, params) => <AgentBuilderPage agentId={params.id} {...props} />,
  },
  {
    pattern: '/use-cases',
    title: 'Use Cases',
    permission: P.TENANT_READ,
    render: () => <UseCasesPage />,
  },
  {
    pattern: '/use-cases/:slug',
    title: 'Use Case Detail',
    permission: P.TENANT_READ,
    render: (props, params) => <UseCasesDetailPage slug={params.slug} {...props} />,
  },
  {
    pattern: '/solutions/use-cases',
    title: 'Use Cases',
    permission: P.TENANT_READ,
    render: () => <UseCasesPage />,
  },
  {
    pattern: '/solutions/use-cases/:slug',
    title: 'Use Case Detail',
    permission: P.TENANT_READ,
    render: (props, params) => <UseCasesDetailPage slug={params.slug} {...props} />,
  },
  {
    pattern: '/industries',
    title: 'Industries',
    permission: P.TENANT_READ,
    render: () => <IndustriesPage />,
  },
  {
    pattern: '/industries/:slug',
    title: 'Industry Detail',
    permission: P.TENANT_READ,
    render: (props, params) => <IndustryDetailPage slug={params.slug} {...props} />,
  },
  {
    pattern: '/integrations',
    title: 'Integrations',
    permission: P.TENANT_READ,
    render: () => <IntegrationsPage />,
  },
  {
    pattern: '/integrations/:slug',
    title: 'Integration Detail',
    permission: P.TENANT_READ,
    render: (props, params) => <IntegrationDetailPage slug={params.slug} {...props} />,
  },
  {
    pattern: '/pricing',
    title: 'Pricing',
    permission: P.TENANT_READ,
    render: () => <PricingPage />,
  },
  {
    pattern: '/developers',
    title: 'Developers',
    permission: P.TENANT_READ,
    render: () => <DevelopersPage />,
  },
  {
    pattern: '/resources',
    title: 'Resources',
    permission: P.TENANT_READ,
    render: () => <ResourcesPage />,
  },
  {
    pattern: '/blog',
    title: 'Blog',
    permission: P.TENANT_READ,
    render: () => <BlogPage />,
  },
  {
    pattern: '/security',
    title: 'Security',
    permission: P.TENANT_READ,
    render: () => <SecurityPage />,
  },
  {
    pattern: '/compliance',
    title: 'Compliance',
    permission: P.TENANT_READ,
    render: () => <CompliancePage />,
  },
  {
    pattern: '/company/about',
    title: 'About',
    permission: P.TENANT_READ,
    render: () => <AboutPage />,
  },
  {
    pattern: '/company/careers',
    title: 'Careers',
    permission: P.TENANT_READ,
    render: () => <CareersPage />,
  },
  {
    pattern: '/company/contact',
    title: 'Contact',
    permission: P.TENANT_READ,
    render: () => <CompanyContactPage />,
  },
  {
    pattern: '/legal/privacy',
    title: 'Privacy',
    permission: P.TENANT_READ,
    render: () => <PrivacyPage />,
  },
  {
    pattern: '/legal/terms',
    title: 'Terms',
    permission: P.TENANT_READ,
    render: () => <TermsPage />,
  },
  {
    pattern: '/product/outbound',
    title: 'Outbound',
    permission: P.TENANT_READ,
    render: () => <OutboundPage />,
  },
  {
    pattern: '/product/inbound',
    title: 'Inbound',
    permission: P.TENANT_READ,
    render: () => <InboundPage />,
  },
  {
    pattern: '/product/voice-cloning',
    title: 'Voice Cloning',
    permission: P.TENANT_READ,
    render: () => <VoiceCloningPage />,
  },
  {
    pattern: '/product/analytics',
    title: 'Analytics',
    permission: P.TENANT_READ,
    render: () => <AnalyticsPage />,
  },
  {
    pattern: '/solutions',
    title: 'Solutions',
    permission: P.TENANT_READ,
    render: () => <SolutionsPage />,
  },

]

/** Resolve a path to `{ route, params }`, or `null` for a 404. */
export function resolveRoute(path) {
  for (const route of ROUTES) {
    const params = match(route.pattern, path)
    if (params) return { route, params }
  }
  return null
}

export default function App() {
  const [me, setMe] = useState(null)
  const [checking, setChecking] = useState(true)
  const path = useRoute()

  // A 401 from anywhere drops the session and returns us to the login screen.
  // `api.js` has already cleared the token by the time this runs.
  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null))
  }, [])

  // Page load: trade the HttpOnly refresh cookie for a session, if there is
  // one. This is unchanged from the legacy App and is the reason a reload
  // does not sign you out even though the access token is only in memory.
  useEffect(() => {
    let alive = true
    bootstrap()
      .then((session) => { if (alive) setMe(session) })
      .finally(() => { if (alive) setChecking(false) })
    return () => { alive = false }
  }, [])

  const can = useMemo(() => makeCan(me?.permissions), [me])

  const onSignOut = useCallback(() => {
    // `logout()` clears the token and fires the unauthorized handler, which
    // is what actually drops `me`. Failure still clears locally.
    logout()
  }, [])

  if (checking) {
    return (
      <div className="boot" role="status" aria-live="polite">
        Loading…
      </div>
    )
  }

  if (!me) {
    return <Login onSuccess={() => bootstrap().then(setMe)} />
  }

  const resolved = resolveRoute(path)

  let title = 'Not found'
  let body

  if (!resolved) {
    body = (
      <EmptyState
        icon="?"
        title="That page does not exist"
        description="The link may be out of date, or the page may not be built yet."
        action={
          <button type="button" className="btn" onClick={() => navigate('/')}>
            Go to overview
          </button>
        }
      />
    )
  } else if (!can(resolved.route.permission)) {
    // Deliberately *not* a redirect. Bouncing someone to the overview for a
    // link a colleague sent them is indistinguishable from a broken link.
    title = resolved.route.title
    body = (
      <EmptyState
        icon="🔒"
        title="You do not have access to this page"
        description="Your role does not include permission to view it. Ask an owner or admin if you need access."
      />
    )
  } else {
    title = resolved.route.title
    body = resolved.route.render({ me, can }, resolved.params)
  }

  return (
    <Shell me={me} can={can} path={path} title={title} onSignOut={onSignOut}>
      {body}
    </Shell>
  )
}