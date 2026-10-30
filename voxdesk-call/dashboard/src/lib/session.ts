import { getEnv } from '../config/env';

export interface AuthSession {
  user: {
    id: string;
    email: string;
    full_name: string;
    role: string;
    tenant_id: string;
    [key: string]: unknown;
  };
  tenant: Record<string, unknown>;
  permissions: string[];
}

let accessToken: string | null = null;
let authSession: AuthSession | null = null;
let refreshInFlight: Promise<string | null> | null = null;
let bootstrapInFlight: Promise<AuthSession | null> | null = null;
let refreshInFlightGeneration = -1;
let bootstrapInFlightGeneration = -1;
let requestSequence = 0;
// Invalidates older async restores after logout or a newer explicit login.
let sessionGeneration = 0;
const listeners = new Set<() => void>();

function emitSessionChange(): void {
  for (const listener of listeners) listener();
}

function removeLegacyToken(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem('voxdesk_access_token');
  } catch {
    // Disabled Web Storage cannot retain the legacy credential either way.
  }
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function getAuthSession(): AuthSession | null {
  return authSession;
}

export function setSessionAccessToken(token: string): void {
  const clean = typeof token === 'string' ? token.trim() : '';
  if (!clean || clean.startsWith('vdpk_') || clean.startsWith('vdws_')) {
    clearSession();
    return;
  }
  sessionGeneration += 1;
  accessToken = clean;
  authSession = null;
  // Remove tokens written by older releases. Account metadata is not an
  // authentication input; bearer tokens never belong in Web Storage.
  removeLegacyToken();
  emitSessionChange();
}

export function clearSession(): void {
  sessionGeneration += 1;
  const changed = accessToken !== null || authSession !== null;
  accessToken = null;
  authSession = null;
  removeLegacyToken();
  if (changed) emitSessionChange();
}

function backendRoot(): string {
  const base = getEnv().apiBaseUrl.replace(/\/+$/, '');
  return base.endsWith('/api') ? base.slice(0, -4) : base;
}

async function fetchSession(path: string, token?: string): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${backendRoot()}${path}`, {
    method: path === '/auth/refresh' ? 'POST' : 'GET',
    headers,
    credentials: 'include',
  });
}

/** Rotate the HttpOnly refresh cookie once across all concurrent callers. */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight && refreshInFlightGeneration === sessionGeneration) {
    return refreshInFlight;
  }
  const generation = sessionGeneration;
  const operation = (async () => {
    try {
      const response = await fetchSession('/auth/refresh');
      if (generation !== sessionGeneration) return null;
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) clearSession();
        return null;
      }
      const body = await response.json() as { access_token?: unknown };
      // Logout or an explicit login while the request was in flight wins over
      // this older response; never resurrect or overwrite that newer state.
      if (generation !== sessionGeneration) return null;
      if (typeof body.access_token !== 'string' || !body.access_token.trim()) {
        clearSession();
        return null;
      }
      setSessionAccessToken(body.access_token);
      return accessToken;
    } catch {
      // Offline/5xx responses are not proof that the user's session ended.
      return null;
    } finally {
      // An older operation must not clear a newer generation's single-flight.
      if (refreshInFlight === operation) {
        refreshInFlight = null;
        refreshInFlightGeneration = -1;
      }
    }
  })();
  refreshInFlight = operation;
  refreshInFlightGeneration = generation;
  return operation;
}

/** Restore the short-lived token and authoritative /auth/me session on load. */
export function bootstrapSession(): Promise<AuthSession | null> {
  if (accessToken && authSession) return Promise.resolve(authSession);
  if (bootstrapInFlight && bootstrapInFlightGeneration === sessionGeneration) {
    return bootstrapInFlight;
  }
  const generationAtStart = sessionGeneration;
  let operation: Promise<AuthSession | null>;
  operation = (async () => {
    let token = accessToken || await refreshAccessToken();
    if (!token) return null;
    let generation = sessionGeneration;
    if (bootstrapInFlight === operation) bootstrapInFlightGeneration = generation;
    try {
      let response = await fetchSession('/auth/me', token);
      if (generation !== sessionGeneration) return null;
      if (response.status === 401) {
        token = await refreshAccessToken();
        if (!token) return null;
        generation = sessionGeneration;
        if (bootstrapInFlight === operation) bootstrapInFlightGeneration = generation;
        response = await fetchSession('/auth/me', token);
        if (generation !== sessionGeneration) return null;
      }
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) clearSession();
        return null;
      }
      const session = await response.json() as AuthSession;
      if (generation !== sessionGeneration) return null;
      if (!session.user || !Array.isArray(session.permissions)) {
        clearSession();
        return null;
      }
      authSession = session;
      emitSessionChange();
      return session;
    } catch {
      return null;
    } finally {
      if (bootstrapInFlight === operation) {
        bootstrapInFlight = null;
        bootstrapInFlightGeneration = -1;
      }
    }
  })();
  bootstrapInFlight = operation;
  bootstrapInFlightGeneration = generationAtStart;
  return operation;
}

export function createRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return `req_${globalThis.crypto.randomUUID()}`;
  }
  requestSequence += 1;
  return `req_${Date.now().toString(36)}_${requestSequence.toString(36)}`;
}
