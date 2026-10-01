"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken } from "@/lib/auth";
import ApiStatus from "./api-status";

// CANONICAL NAVIGATION per P0-01, P0-02, OTHER-02 audit + P1-05, P1-08
// Unified Agent Factory IA: contact-center-first → agent-factory-first
// Preserves all existing routes (Overview/Calls/Analytics/Appointments/Campaigns/Leads/Knowledge/Integrations/Agent/Team/Billing/Audit/Settings)
// Adds product control plane surfaces: Specialized Agents, Workflow Builder, Voice, Governance, Deployment, Forecasting, Insight, Translation, Anomaly, etc.
// Order: Core → Agent Factory → Operations → Intelligence → Governance → System

const CORE_LINKS = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/calls", label: "Calls" },
  { href: "/dashboard/analytics", label: "Analytics" },
];

const AGENT_FACTORY_LINKS = [
  { href: "/dashboard/specialized-agents", label: "Specialized Agents", badge: "P0-02" },
  { href: "/dashboard/workflows", label: "Workflow Builder", badge: "P0-03" },
  { href: "/dashboard/voice", label: "Voice Agent", badge: "P0-04" },
  { href: "/dashboard/agent", label: "Agent Settings" },
];

const OPERATIONS_LINKS = [
  { href: "/dashboard/appointments", label: "Appointments" },
  { href: "/dashboard/campaigns", label: "Campaigns" },
  { href: "/dashboard/leads", label: "Leads" },
  { href: "/dashboard/knowledge", label: "Knowledge" },
  { href: "/dashboard/integrations", label: "Integrations" },
];

const INTELLIGENCE_LINKS = [
  { href: "/dashboard/forecasting", label: "Forecasting", badge: "P1" },
  { href: "/dashboard/insight", label: "Insight", badge: "P1" },
  { href: "/dashboard/translation", label: "Translation", badge: "P0-P1" },
  { href: "/dashboard/anomaly", label: "Anomaly Detection", badge: "P1" },
];

const GOVERNANCE_LINKS = [
  { href: "/dashboard/governance", label: "Governance", badge: "P0-05" },
  { href: "/dashboard/reviews", label: "Reviews" },
  { href: "/dashboard/evidence", label: "Evidence" },
  { href: "/dashboard/compliance", label: "Compliance", badge: "P1-03" },
  { href: "/dashboard/legal", label: "Legal Playbook", badge: "P1-04" },
  { href: "/dashboard/roi", label: "ROI / Analytics", badge: "P1-06" },
];

const SYSTEM_LINKS = [
  { href: "/dashboard/deployment", label: "Deployment", badge: "P1-07" },
  { href: "/dashboard/connection-demo", label: "Full-Stack Demo", badge: "CSS+TS+API" },
  { href: "/dashboard/team", label: "Team" },
  { href: "/dashboard/billing", label: "Billing" },
  { href: "/dashboard/audit", label: "Audit" },
  { href: "/dashboard/settings", label: "Settings" },
];

export default function ShellNav() {
  const pathname = usePathname();
  const router = useRouter();

  function signOut() {
    clearToken();
    router.replace("/login");
  }

  function isActive(href: string): boolean {
    if (href === "/dashboard") return pathname === "/dashboard";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <nav className="sidebar">
      <div className="brand">
        VoxDesk
        <span className="brand-subtitle">Agent Factory</span>
      </div>
      
      <div className="nav-section">
        <div className="nav-section-title">Core</div>
        <ul>
          {CORE_LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className={isActive(link.href) ? "active" : undefined}>
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="nav-section">
        <div className="nav-section-title">Agent Factory (P0)</div>
        <ul>
          {AGENT_FACTORY_LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className={isActive(link.href) ? "active" : undefined}>
                {link.label}
                {(link as any).badge && <span className="nav-badge">{(link as any).badge}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="nav-section">
        <div className="nav-section-title">Operations</div>
        <ul>
          {OPERATIONS_LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className={isActive(link.href) ? "active" : undefined}>
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="nav-section">
        <div className="nav-section-title">Intelligence (P1)</div>
        <ul>
          {INTELLIGENCE_LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className={isActive(link.href) ? "active" : undefined}>
                {link.label}
                {(link as any).badge && <span className="nav-badge">{(link as any).badge}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="nav-section">
        <div className="nav-section-title">Governance</div>
        <ul>
          {GOVERNANCE_LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className={isActive(link.href) ? "active" : undefined}>
                {link.label}
                {(link as any).badge && <span className="nav-badge">{(link as any).badge}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="nav-section">
        <div className="nav-section-title">System</div>
        <ul>
          {SYSTEM_LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className={isActive(link.href) ? "active" : undefined}>
                {link.label}
                {(link as any).badge && <span className="nav-badge">{(link as any).badge}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="nav-footer">
        <div style={{ marginBottom: 12 }}>
          <ApiStatus />
        </div>
        <div className="canonical-info">
          <small>Canonical: dashboard (Vite)</small>
          <small>Shadow: dashboard-next (roadmap)</small>
          <small>Realtime: gateway-go+media-engine-rs</small>
          <small>Connectors: 21 (12 enterprise)</small>
          <small>Agents: 16 (5 core+6 legal+4 compliance)</small>
          <small>Routes: 39 Next + 84 FastAPI</small>
          <small>CSS: agent-factory.css + globals.css</small>
          <small>TS: agent-factory-types.ts + api.ts</small>
        </div>
        <button className="sign-out" type="button" onClick={signOut}>
          Sign out
        </button>
      </div>
    </nav>
  );
}
