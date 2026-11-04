export interface NavItem { id:string; label:string; href:string; external?:boolean; badge?:string; children?:NavItem[]; }
export const publicNavigation: NavItem[]=[
  {id:'product',label:'Product',href:'/product',children:[{id:'voice-agents',label:'Voice Agents',href:'/product/voice-agents'},{id:'knowledge',label:'Knowledge Base',href:'/product/knowledge'},{id:'analytics',label:'Analytics',href:'/product/analytics'}]},
  {id:'solutions',label:'Solutions',href:'/solutions',children:[{id:'support',label:'Customer Support',href:'/solutions/support'},{id:'appointments',label:'Appointment Booking',href:'/solutions/appointments'},{id:'lead-qual',label:'Lead Qualification',href:'/solutions/lead-qualification'},{id:'outbound',label:'Outbound Campaigns',href:'/solutions/outbound'}]},
  {id:'developers',label:'Developers',href:'/developers',children:[{id:'api',label:'API Reference',href:'/developers/api'},{id:'webhooks',label:'Webhooks',href:'/developers/webhooks'},{id:'sdk',label:'SDK',href:'/developers/sdk'}]},
  {id:'pricing',label:'Pricing',href:'/pricing'},
  {id:'resources',label:'Resources',href:'/resources',children:[{id:'docs',label:'Documentation',href:'/docs'},{id:'blog',label:'Blog',href:'/blog'},{id:'status',label:'Status',href:'/status',external:true}]},
];
export const footerNavigation: Record<string,NavItem[]>={
  product:[{id:'voice-agents',label:'Voice Agents',href:'/product/voice-agents'},{id:'knowledge',label:'Knowledge Base',href:'/product/knowledge'},{id:'analytics',label:'Analytics',href:'/product/analytics'},{id:'phone',label:'Phone Numbers',href:'/product/phone'},{id:'campaigns',label:'Campaigns',href:'/product/campaigns'}],
  solutions:[{id:'support',label:'Customer Support',href:'/solutions/support'},{id:'appointments',label:'Appointment Booking',href:'/solutions/appointments'},{id:'lead',label:'Lead Qualification',href:'/solutions/lead-qualification'},{id:'receptionist',label:'Receptionist',href:'/solutions/receptionist'}],
  developers:[{id:'api',label:'API Reference',href:'/developers/api'},{id:'webhooks',label:'Webhooks',href:'/developers/webhooks'},{id:'sdk',label:'SDK',href:'/developers/sdk'},{id:'tools',label:'Tools',href:'/developers/tools'}],
  company:[{id:'about',label:'About',href:'/about'},{id:'careers',label:'Careers',href:'/careers'},{id:'contact',label:'Contact',href:'/contact'}],
};
export const ctaNavigation={ login:{label:'Login',href:'/login'}, startBuilding:{label:'Start Building',href:'/signup'}, bookDemo:{label:'Book Demo',href:'/demo'}, };

/**
 * Authenticated operator-console navigation.
 *
 * Rendered by `components/layout/ConsoleNav.tsx`, which additionally filters each
 * item against the permission list returned by `GET /auth/me`. The `permission`
 * field here must stay in sync with that filter — hiding a link is convenience,
 * the server still enforces every one of these.
 */
export const consoleNavigation: NavItem[] = [
  { id:'console-overview', label:'Overview', href:'/app/overview' },
  { id:'console-campaigns', label:'Campaigns', href:'/dashboard/campaigns' },
  { id:'console-analytics', label:'Analytics', href:'/dashboard/analytics-console' },
  { id:'console-knowledge', label:'Knowledge base', href:'/dashboard/knowledge-base' },
  { id:'console-integrations', label:'Integrations', href:'/dashboard/integrations-console' },
  { id:'console-team', label:'Team', href:'/dashboard/team' },
  { id:'console-billing', label:'Billing', href:'/dashboard/billing' },
  { id:'console-webhooks', label:'Webhooks', href:'/dashboard/webhooks' },
  { id:'console-audit', label:'Audit log', href:'/dashboard/audit-log' },
  { id:'console-workspace-settings', label:'Workspace settings', href:'/dashboard/settings' },
  { id:'console-identity-settings', label:'Identity settings', href:'/dashboard/settings/identity' },
];
