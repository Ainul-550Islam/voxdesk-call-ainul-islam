/**
 * dashboard/src/app/app.tsx
 * Unified Application Shell — Public Business Site + Auth Boundary + Agent Studio + Operator Console
 */
import React, { useEffect, useMemo, useState } from 'react';
import { hasAuthenticatedSessionToken } from '../api/public-site';
import OperatorConsoleApp from '../App';
import { WidgetSettings } from '../features/public-widget/WidgetSettings';
import { AgentArchivePage } from '../pages/agents/AgentArchivePage';
import { AgentBuilderPage } from '../pages/agents/AgentBuilderPage';
import { AgentDetailPage } from '../pages/agents/AgentDetailPage';
import { AgentDuplicatePage } from '../pages/agents/AgentDuplicatePage';
import { AgentKnowledgePage } from '../pages/agents/AgentKnowledgePage';
import { AgentListPage } from '../pages/agents/AgentListPage';
import { AgentModelPage } from '../pages/agents/AgentModelPage';
import { AgentSettingsPage } from '../pages/agents/AgentSettingsPage';
import { AgentsPage } from '../pages/agents/AgentsPage';
import { AgentTestHistoryPage } from '../pages/agents/AgentTestHistoryPage';
import { AgentToolsPage } from '../pages/agents/AgentToolsPage';
import { AgentVersionDetailPage } from '../pages/agents/AgentVersionDetailPage';
import { AgentVoicePage } from '../pages/agents/AgentVoicePage';
import { CreateAgentPage } from '../pages/agents/CreateAgentPage';
import { BlogPage } from '../pages/blog/BlogPage';
import { BlogPost } from '../pages/blog/BlogPost';
import { ChatAgentsPage } from '../pages/chat/ChatAgentsPage';
import { AboutPage } from '../pages/company/AboutPage';
import { CareersPage } from '../pages/company/CareersPage';
import { TeamPage } from '../pages/company/TeamPage';
import { CompliancePage } from '../pages/compliance/CompliancePage';
import { ConductorPage } from '../pages/conductor/ConductorPage';
import { BookDemoPage } from '../pages/contact/BookDemoPage';
import { ContactsPage } from '../pages/contacts/ContactsPage';
import { DevelopersPage } from '../pages/developers/DevelopersPage';
import { DocsPage } from '../pages/docs/DocsPage';
import { HomePage } from '../pages/home/HomePage';
import { IndustriesPage } from '../pages/industries/IndustriesPage';
import { IndustryDetailPage } from '../pages/industries/IndustryDetailPage';
import { IntegrationDetailPage } from '../pages/integrations/IntegrationDetailPage';
import { IntegrationsPage } from '../pages/integrations/IntegrationsPage';
import { DPA } from '../pages/legal/DPA';
import { PrivacyPage } from '../pages/legal/PrivacyPage';
import { SLA } from '../pages/legal/SLA';
import { TermsPage } from '../pages/legal/TermsPage';
import { PricingPage } from '../pages/pricing/PricingPage';
import { AgentPlayground } from '../pages/product/AgentPlayground';
import { AnalyticsPage } from '../pages/product/analytics/AnalyticsPage';
import { CallRuntimePage } from '../pages/CallRuntimePage';
import { PhoneNumbersPage } from '../pages/PhoneNumbersPage';
import { FinalParityPage } from '../pages/FinalParityPage';
import { WorkflowsPage } from '../pages/WorkflowsPage';
import { AnsweringServicePage } from '../pages/product/answering-service/AnsweringServicePage';
import { AppointmentSetterPage } from '../pages/product/appointment-setter/AppointmentSetterPage';
import { CustomerServicePage } from '../pages/product/customer-service/CustomerServicePage';
import { InboundPage } from '../pages/product/inbound/InboundPage';
import { OutboundPage } from '../pages/product/outbound/OutboundPage';
import { QAScorecards } from '../pages/product/QAScorecards';
import { Simulations } from '../pages/product/Simulations';
import { TelemarketingPage } from '../pages/product/telemarketing/TelemarketingPage';
import { VoiceAgentsPage } from '../pages/product/voice-agents/VoiceAgentsPage';
import { VoiceCloningPage } from '../pages/product/voice-cloning/VoiceCloningPage';
import { ContactSalesPage } from '../pages/public/ContactSalesPage';
import { LoginPage } from '../pages/public/LoginPage';
import { SignupPage } from '../pages/public/SignupPage';
import { StatusPage } from '../pages/public/StatusPage';
import { ResourcesPage } from '../pages/resources/ResourcesPage';
import { SecurityPage } from '../pages/security/SecurityPage';
import { SolutionsPage } from '../pages/solutions/SolutionsPage';
import { TrustPage } from '../pages/trust/TrustPage';
import { UseCasesDetailPage } from '../pages/use-cases/UseCasesDetailPage';
import { UseCasesPage } from '../pages/use-cases/UseCasesPage';
import { isProtectedPath, matchRoute } from './router';

function LegacyConsoleLoading({ title }: { title: string }) {
  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#080c12', color: '#e9eef5' }}>
      <p role="status" aria-live="polite">Opening {title}…</p>
    </main>
  );
}

export function App() {
  const [pathname, setPathname] = useState<string>(
    typeof window !== 'undefined' ? window.location.pathname : '/',
  );
  const [hash, setHash] = useState<string>(
    typeof window !== 'undefined' ? window.location.hash : '',
  );

  useEffect(() => {
    const handleLocationChange = () => {
      setPathname(window.location.pathname);
      setHash(window.location.hash);
    };

    const handleClick = (e: MouseEvent) => {
      if (
        e.defaultPrevented ||
        e.button !== 0 ||
        e.metaKey ||
        e.ctrlKey ||
        e.shiftKey ||
        e.altKey
      ) {
        return;
      }
      const anchor = (e.target as HTMLElement | null)?.closest?.('a');
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (
        !href ||
        href.startsWith('http://') ||
        href.startsWith('https://') ||
        href.startsWith('mailto:') ||
        href.startsWith('tel:')
      ) {
        return;
      }
      if (href.startsWith('#/')) {
        return;
      }
      if (href.startsWith('/')) {
        e.preventDefault();
        window.history.pushState(null, '', href);
        setPathname(window.location.pathname);
        setHash(window.location.hash);
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    };

    window.addEventListener('popstate', handleLocationChange);
    window.addEventListener('hashchange', handleLocationChange);
    document.addEventListener('click', handleClick);
    return () => {
      window.removeEventListener('popstate', handleLocationChange);
      window.removeEventListener('hashchange', handleLocationChange);
      document.removeEventListener('click', handleClick);
    };
  }, []);

  useEffect(() => {
    const isCallRoute = pathname === '/calls' || pathname.startsWith('/calls/');
    const isDashboardCallRoute = pathname === '/dashboard/calls' || pathname.startsWith('/dashboard/calls/');
    if (!isCallRoute && !isDashboardCallRoute) return;

    const consolePath = pathname.replace(/^\/dashboard\/calls(?=\/|$)/, '/calls');
    if (!window.location.hash.startsWith('#/')) {
      window.location.hash = consolePath;
    }
  }, [pathname]);

  const matched = useMemo(() => matchRoute(pathname), [pathname]);
  const isAuthenticated = hasAuthenticatedSessionToken();

  useEffect(() => {
    const legacyPath = matched?.route.legacyPath;
    if (!legacyPath) return;
    const expectedHash = `#${legacyPath}`;
    if (window.location.hash !== expectedHash) {
      window.location.hash = legacyPath;
    }
  }, [matched, pathname]);

  useEffect(() => {
    if (matched?.route.title) {
      document.title = matched.route.title;
    }
  }, [matched]);

  // Enforce Protected Route Boundary: unauthenticated requests to /app/* or /dashboard/*
  // render LoginPage with a safe local return path (`next`) and never mount private consoles.
  if (isProtectedPath(pathname) && !isAuthenticated) {
    return (
      <LoginPage
        nextPath={pathname}
        onLoginSuccess={(redirectTo) => {
          if (typeof window !== 'undefined') {
            window.history.pushState(null, '', redirectTo);
            setPathname(window.location.pathname);
          }
        }}
      />
    );
  }

  // If hash routing is active (e.g. #/overview, #/calls, #/billing) or /workspace path, mount OperatorConsoleApp
  if (hash.startsWith('#/') || pathname.startsWith('/workspace')) {
    return <OperatorConsoleApp />;
  }

  if (!matched) {
    return <HomePage />;
  }

  const { route, params } = matched;
  const slug = params.slug || '';
  const id = params.id || '';

  switch (route.component) {
    case 'HomePage':
      return <HomePage />;
    case 'VoiceAgentsPage':
      return <VoiceAgentsPage />;
    case 'CustomerServicePage':
      return <CustomerServicePage />;
    case 'AnsweringServicePage':
      return <AnsweringServicePage />;
    case 'AppointmentSetterPage':
      return <AppointmentSetterPage />;
    case 'TelemarketingPage':
      return <TelemarketingPage />;
    case 'OutboundPage':
      return <OutboundPage />;
    case 'InboundPage':
      return <InboundPage />;
    case 'AnalyticsPage':
      return <AnalyticsPage />;
    case 'PhoneNumbersPage':
      return <PhoneNumbersPage />;
    case 'CallRuntimePage':
      return <CallRuntimePage />;
    case 'FinalParityPage':
      return <FinalParityPage />;
    case 'WorkflowsPage':
      return <WorkflowsPage />;
    case 'LegacyCampaignsPage':
      return <LegacyConsoleLoading title="Campaigns" />;
    case 'LegacySettingsPage':
      return <LegacyConsoleLoading title="Security & settings" />;
    case 'LegacyBillingPage':
      return <LegacyConsoleLoading title="Billing" />;
    case 'CallLogConsole':
      return <OperatorConsoleApp />;
    case 'VoiceCloningPage':
      return <VoiceCloningPage />;
    case 'SolutionsPage':
      return <SolutionsPage />;
    case 'UseCasesPage':
      return <UseCasesPage />;
    case 'UseCasesDetailPage':
      return <UseCasesDetailPage slug={slug} />;
    case 'IndustriesPage':
      return <IndustriesPage />;
    case 'IndustryDetailPage':
      return <IndustryDetailPage slug={slug} />;
    case 'IntegrationsPage':
      return <IntegrationsPage />;
    case 'IntegrationDetailPage':
      return <IntegrationDetailPage slug={slug} />;
    case 'PricingPage':
      return <PricingPage />;
    case 'DevelopersPage':
      return <DevelopersPage />;
    case 'DocsPage':
      return <DocsPage slug={slug} />;
    case 'SecurityPage':
      return <SecurityPage />;
    case 'TrustPage':
      return <TrustPage />;
    case 'StatusPage':
      return <StatusPage />;
    case 'CompliancePage':
      return <CompliancePage />;
    case 'ResourcesPage':
      return <ResourcesPage />;
    case 'BlogPage':
      return <BlogPage />;
    case 'BlogPost':
      return <BlogPost slug={slug} />;
    case 'AboutPage':
      return <AboutPage />;
    case 'CareersPage':
      return <CareersPage />;
    case 'TeamPage':
      return <TeamPage />;
    case 'ContactPage':
    case 'ContactSalesPage':
      return <ContactSalesPage />;
    case 'BookDemoPage':
      return <BookDemoPage />;
    case 'LoginPage':
      return <LoginPage />;
    case 'SignupPage':
      return <SignupPage />;
    case 'PrivacyPage':
      return <PrivacyPage />;
    case 'TermsPage':
      return <TermsPage />;
    case 'DPA':
      return <DPA />;
    case 'SLA':
      return <SLA />;
    case 'AgentsPage':
      return <AgentsPage />;
    case 'CreateAgentPage':
      return <CreateAgentPage />;
    case 'AgentDetailPage':
      return <AgentDetailPage agentId={id} />;
    case 'AgentBuilderPage':
      return <AgentBuilderPage agentId={id} />;
    case 'AgentVersionDetailPage':
      return (
        <AgentVersionDetailPage
          agentId={id}
          versionNumber={Number(params.version || 1)}
        />
      );
    case 'AgentSettingsPage':
      return <AgentSettingsPage agentId={id} />;
    case 'AgentListPage':
      return <AgentListPage />;
    case 'AgentKnowledgePage':
      return <AgentKnowledgePage />;
    case 'AgentArchivePage':
      return <AgentArchivePage />;
    case 'AgentVoicePage':
      return <AgentVoicePage agentId={id} />;
    case 'AgentModelPage':
      return <AgentModelPage agentId={id} />;
    case 'AgentToolsPage':
      return <AgentToolsPage agentId={id} />;
    case 'AgentTestHistoryPage':
      return <AgentTestHistoryPage agentId={id} />;
    case 'AgentDuplicatePage':
      return <AgentDuplicatePage agentId={id} />;
    case 'WidgetSettings':
      return (
        <div style={{ minHeight: '100vh', background: '#06090F', padding: 28 }}>
          <div style={{ maxWidth: 1120, margin: '0 auto' }}>
            <WidgetSettings />
          </div>
        </div>
      );
    case 'ChatAgentsPage':
      return <ChatAgentsPage />;
    case 'ContactsPage':
      return <ContactsPage />;
    case 'AgentPlaygroundPage':
      return <AgentPlayground />;
    case 'SimulationsPage':
      return <Simulations />;
    case 'QAScorecardsPage':
      return <QAScorecards />;
    case 'ConductorPage':
      return <ConductorPage />;
    case 'OperatorConsole':
      return <OperatorConsoleApp />;
    default:
      return <HomePage />;
  }
}

export default App;
