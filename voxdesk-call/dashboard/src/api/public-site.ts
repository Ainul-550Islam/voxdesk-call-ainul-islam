/**
 * dashboard/src/api/public-site.ts
 * Public website, auth boundary, login, signup, and contact-sales API client.
 * Never attaches private workspace bearer tokens to public site marketing endpoints.
 */

import type {
  PublicContactSalesInput,
  PublicContactSalesResponse,
  PublicLoginResponse,
  PublicPricingTier,
  PublicSiteManifest,
  PublicSiteStatusSummary,
  PublicSignupInput,
  PublicSignupResponse,
} from './types/public-widget';

export class PublicSiteApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'PublicSiteApiError';
    this.status = status;
    this.code = code;
  }
}

async function publicJsonFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    ...((options.headers as Record<string, string>) || {}),
  };

  const response = await fetch(path, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let code = `HTTP_${response.status}`;
    let message = response.statusText || 'Request failed';
    try {
      const body = await response.json();
      if (body?.error?.code) code = String(body.error.code);
      if (body?.error?.message) message = String(body.error.message);
      else if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      // ignore json parse failure
    }
    throw new PublicSiteApiError(response.status, code, message);
  }

  return (await response.json()) as T;
}

/**
 * Client-side open-redirect guard matching server-side validate_safe_next_path.
 * Only relative single-slash local paths outside /login and /signup are accepted.
 */
export function sanitizeReturnPath(
  rawNext: string | null | undefined,
  defaultPath = '/app/overview',
): string {
  if (!rawNext || typeof rawNext !== 'string') {
    return defaultPath;
  }
  const candidate = rawNext.trim();
  if (!candidate) {
    return defaultPath;
  }
  if (
    !candidate.startsWith('/') ||
    candidate.startsWith('//') ||
    candidate.startsWith('/\\') ||
    candidate.includes('://') ||
    candidate.includes('\r') ||
    candidate.includes('\n')
  ) {
    return defaultPath;
  }
  const pathPart = candidate.split('?')[0].split('#')[0];
  if (pathPart === '/login' || pathPart === '/signup' || pathPart === '/logout') {
    return defaultPath;
  }
  return candidate;
}

export async function fetchPublicSiteManifest(): Promise<PublicSiteManifest> {
  return publicJsonFetch<PublicSiteManifest>('/api/v1/public/site/manifest', {
    credentials: 'omit',
  });
}

export async function fetchPublicPricingTiers(): Promise<PublicPricingTier[]> {
  return publicJsonFetch<PublicPricingTier[]>('/api/v1/public/site/pricing', {
    credentials: 'omit',
  });
}

export async function fetchPublicStatusSummary(): Promise<PublicSiteStatusSummary> {
  return publicJsonFetch<PublicSiteStatusSummary>('/api/v1/public/site/status', {
    credentials: 'omit',
  });
}

export async function submitPublicContactSales(
  payload: PublicContactSalesInput,
): Promise<PublicContactSalesResponse> {
  return publicJsonFetch<PublicContactSalesResponse>(
    '/api/v1/public/contact-sales',
    {
      method: 'POST',
      body: JSON.stringify(payload),
      credentials: 'omit',
    },
  );
}

export async function loginWithPassword(params: {
  email: string;
  password: string;
  tenant_id?: string | null;
  next_path?: string | null;
}): Promise<PublicLoginResponse> {
  return publicJsonFetch<PublicLoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({
      email: params.email.trim(),
      password: params.password,
      tenant_id: params.tenant_id || undefined,
      next: params.next_path || undefined,
    }),
  });
}

export async function signupWorkspace(
  payload: PublicSignupInput,
): Promise<PublicSignupResponse> {
  return publicJsonFetch<PublicSignupResponse>('/auth/signup', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function validateServerNextPath(nextPath: string): Promise<{
  valid: boolean;
  redirect_to: string;
  rejected_reason?: string | null;
}> {
  const qs = new URLSearchParams({ next: nextPath });
  return publicJsonFetch(`/auth/validate-next?${qs.toString()}`);
}

export function persistAuthenticatedSession(params: {
  accessToken: string;
  user?: {
    id: string;
    email: string;
    full_name: string;
    role: string;
    tenant_id: string;
  } | null;
}): void {
  if (typeof window === 'undefined' || !window.localStorage) return;
  window.localStorage.setItem('voxdesk_access_token', params.accessToken);
  if (params.user) {
    window.localStorage.setItem('voxdesk_user', JSON.stringify(params.user));
    window.localStorage.setItem('voxdesk_tenant_id', params.user.tenant_id);
  }
}

export function clearAuthenticatedSession(): void {
  if (typeof window === 'undefined' || !window.localStorage) return;
  window.localStorage.removeItem('voxdesk_access_token');
  window.localStorage.removeItem('voxdesk_user');
  window.localStorage.removeItem('voxdesk_tenant_id');
}

export function hasAuthenticatedSessionToken(): boolean {
  if (typeof window === 'undefined' || !window.localStorage) return false;
  const token = window.localStorage.getItem('voxdesk_access_token');
  if (!token || !token.trim()) return false;
  // Never treat a public key or widget token as an authenticated workspace session
  if (token.startsWith('vdpk_') || token.startsWith('vdws_')) {
    return false;
  }
  return true;
}
