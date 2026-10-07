/**
 * dashboard/src/api/types/public-widget.ts
 * Prompt 5: Public Website, Auth Boundary, Scoped Public Keys, and Web Widget TypeScript contracts.
 */

export type PublicRouteCategory = 'public' | 'auth' | 'protected' | 'public_widget';

export type PublicWidgetKeyStatus = 'active' | 'rotated' | 'revoked' | 'expired';

export type PublicWidgetCapability =
  | 'widget_config_read'
  | 'widget_session_create'
  | 'widget_chat_send'
  | 'widget_voice_start'
  | 'widget_session_end';

export type PublicWidgetSessionMode = 'chat' | 'voice';

export type PublicWidgetSessionStatus =
  | 'ready'
  | 'connecting'
  | 'connected'
  | 'completed'
  | 'expired'
  | 'failed'
  | 'not_configured'
  | 'forbidden_origin'
  | 'invalid_public_key';

export type PublicWidgetUiState =
  | 'IDLE'
  | 'VALIDATING_KEY'
  | 'READY'
  | 'CONNECTING'
  | 'CONNECTED'
  | 'SPEAKING'
  | 'LISTENING'
  | 'ENDED'
  | 'EXPIRED'
  | 'FORBIDDEN_ORIGIN'
  | 'INVALID_PUBLIC_KEY'
  | 'NOT_CONFIGURED'
  | 'RATE_LIMITED'
  | 'ERROR';

export interface PublicWidgetAppearanceConfig {
  title: string;
  subtitle: string;
  greeting: string;
  placeholder: string;
  primary_color: string;
  position: string;
  enable_chat: boolean;
  enable_voice: boolean;
  show_branding: boolean;
}

export interface PublicWidgetKeyCreateInput {
  agent_id: string;
  agent_kind?: 'voice' | 'chat';
  name: string;
  environment_name?: string;
  allowed_origins: string[];
  allowed_capabilities?: PublicWidgetCapability[];
  rate_limit_per_minute?: number;
  session_ttl_seconds?: number;
  require_published_agent?: boolean;
  expires_in_days?: number | null;
  widget_config?: Partial<PublicWidgetAppearanceConfig>;
}

export interface PublicWidgetKeyUpdateInput {
  name?: string;
  allowed_origins?: string[];
  allowed_capabilities?: PublicWidgetCapability[];
  rate_limit_per_minute?: number;
  session_ttl_seconds?: number;
  require_published_agent?: boolean;
  widget_config?: Partial<PublicWidgetAppearanceConfig>;
}

export interface PublicWidgetKeyRecord {
  id: string;
  tenant_id: string;
  environment_id?: string | null;
  environment_name: string;
  agent_id: string;
  agent_kind: string;
  name: string;
  key_prefix: string;
  status: PublicWidgetKeyStatus;
  allowed_origins: string[];
  allowed_capabilities: string[];
  rate_limit_per_minute: number;
  session_ttl_seconds: number;
  require_published_agent: boolean;
  widget_config: PublicWidgetAppearanceConfig;
  rotated_from_key_id?: string | null;
  rotated_to_key_id?: string | null;
  created_by?: string | null;
  revoked_by?: string | null;
  revoke_reason?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  rotated_at?: string | null;
  last_used_at?: string | null;
  last_used_origin?: string | null;
  created_at: string;
  updated_at: string;
  embed_snippet: string;
}

export interface PublicWidgetKeyCreatedResponse {
  key: PublicWidgetKeyRecord;
  public_key: string;
  warning: string;
}

export interface PublicWidgetBootstrapConfig {
  public_key_prefix: string;
  agent_id: string;
  agent_name: string;
  agent_kind: string;
  published_version_number: number;
  environment_name: string;
  allowed_capabilities: string[];
  appearance: PublicWidgetAppearanceConfig;
  voice_transport_configured: boolean;
  voice_transport_status: string;
  voice_transport_message?: string | null;
  session_ttl_seconds: number;
}

export interface PublicWidgetTurnRecord {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  turn_index: number;
}

export interface PublicWidgetSessionRecord {
  session_id: string;
  status: PublicWidgetSessionStatus;
  mode: PublicWidgetSessionMode;
  agent_id: string;
  agent_name: string;
  agent_kind: string;
  agent_version_number: number;
  transport: string;
  session_token?: string | null;
  expires_at: string;
  turns_count: number;
  max_turns: number;
  transcript: PublicWidgetTurnRecord[];
  appearance: PublicWidgetAppearanceConfig;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  ended_at?: string | null;
}

export interface PublicWidgetMessageSendResponse {
  session_id: string;
  status: PublicWidgetSessionStatus;
  turn_index: number;
  user_turn: PublicWidgetTurnRecord;
  assistant_turn: PublicWidgetTurnRecord;
  turns_count: number;
  max_turns: number;
  remaining_turns: number;
  transcript: PublicWidgetTurnRecord[];
}

export interface PublicNavItem {
  id: string;
  label: string;
  href: string;
  category: string;
  requires_auth: boolean;
}

export interface PublicPricingTier {
  id: string;
  code: string;
  name: string;
  catalogue_source: 'DATABASE' | 'SEED_DEFAULT';
  description: string;
  monthly_price_cents: number;
  annual_price_cents: number | null;
  currency: string;
  included_minutes: number;
  included_sms_segments: number;
  included_llm_tokens: number;
  included_tts_characters: number;
  max_concurrency: number | null;
  overage_enabled: boolean;
  trial_days: number;
  features: string[];
  cta_label: string;
  cta_href: string;
  is_enterprise: boolean;
}

export interface PublicStatusComponent {
  id: string;
  name: string;
  status: string;
  description: string;
  updated_at: string;
}

export interface PublicSiteStatusSummary {
  overall_status: string;
  components: PublicStatusComponent[];
  checked_at: string;
  message: string;
}

export interface PublicSiteManifest {
  site_name: string;
  tagline: string;
  canonical_origin: string;
  public_routes: string[];
  protected_route_prefixes: string[];
  navigation: PublicNavItem[];
  capabilities: Array<Record<string, unknown>>;
  use_cases: Array<Record<string, unknown>>;
  security_highlights: Array<Record<string, unknown>>;
  pricing_tiers: PublicPricingTier[];
  status_summary: PublicSiteStatusSummary;
  seo_meta: {
    title: string;
    description: string;
    canonical_url: string;
    robots: string;
    og_type: string;
  };
}

export interface PublicContactSalesInput {
  full_name: string;
  work_email: string;
  company_name: string;
  job_title?: string;
  phone_number?: string;
  monthly_call_volume?: string;
  primary_use_case?: string;
  message?: string;
  source_path?: string;
}

export interface PublicContactSalesResponse {
  id: string;
  full_name: string;
  work_email: string;
  company_name: string;
  job_title: string;
  phone_number?: string | null;
  monthly_call_volume: string;
  primary_use_case: string;
  status: string;
  created_at: string;
  confirmation_message: string;
}

export interface PublicAuthUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  tenant_id: string;
  is_active: boolean;
}

export interface PublicLoginResponse {
  access_token?: string;
  token_type?: string;
  expires_in?: number;
  user?: PublicAuthUser;
  mfa_enrollment_required?: boolean;
  mfa_required?: boolean;
  challenge?: string;
  methods?: string[];
  redirect_to: string;
}

export interface PublicSignupInput {
  organization_name: string;
  full_name: string;
  email: string;
  password: string;
  industry?: string;
  next_path?: string | null;
}

export interface PublicSignupResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: PublicAuthUser;
  tenant_id: string;
  organization_name: string;
  redirect_to: string;
}
