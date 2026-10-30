export interface EnvConfig {
  apiBaseUrl: string;
  publicBaseUrl: string;
  appEnv: 'development' | 'staging' | 'production';
  enableVoiceDemo: boolean;
  enableAnalytics: boolean;
  version: string;
}

function getEnvVar(key: string, fallback: string): string {
  const viteEnv = (import.meta as any).env?.[key];
  if (viteEnv !== undefined && viteEnv !== null && String(viteEnv).trim()) return String(viteEnv);
  if (typeof window !== 'undefined') {
    const runtimeValue = (window as any).__ENV__?.[key];
    if (runtimeValue !== undefined && runtimeValue !== null && String(runtimeValue).trim()) {
      return String(runtimeValue);
    }
  }
  return fallback;
}

function normalizeBaseUrl(value: string, key: string, allowRelative: boolean): string {
  const clean = value.trim().replace(/\/+$/, '');
  if (!clean || /[\r\n]/.test(clean)) throw new Error(`${key} must not be empty`);
  if (allowRelative && clean.startsWith('/') && !clean.startsWith('//')) {
    if (/[?#]/.test(clean)) throw new Error(`${key} must be an absolute path without a query or fragment`);
    return clean;
  }
  let parsed: URL;
  try {
    parsed = new URL(clean);
  } catch {
    throw new Error(`${key} must be an HTTP(S) URL${allowRelative ? ' or an absolute path' : ''}`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${key} contains an unsupported URL`);
  }
  return clean;
}

function booleanValue(value: string, key: string): boolean {
  const normalized = value.trim().toLowerCase();
  if (normalized === 'true') return true;
  if (normalized === 'false') return false;
  throw new Error(`${key} must be true or false`);
}

let cachedEnv: EnvConfig | null = null;

export function getEnv(): EnvConfig {
  if (cachedEnv) return cachedEnv;
  const origin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
  const appEnv = getEnvVar('VITE_APP_ENV', 'development');
  if (!['development', 'staging', 'production'].includes(appEnv)) {
    throw new Error('VITE_APP_ENV must be development, staging, or production');
  }
  const apiBaseUrl = normalizeBaseUrl(getEnvVar('VITE_API_BASE_URL', '/api'), 'VITE_API_BASE_URL', true);
  const publicBaseUrl = normalizeBaseUrl(getEnvVar('VITE_PUBLIC_BASE_URL', origin), 'VITE_PUBLIC_BASE_URL', false);
  if (appEnv === 'production') {
    if (!apiBaseUrl.startsWith('/') && new URL(apiBaseUrl).protocol !== 'https:') {
      throw new Error('VITE_API_BASE_URL must use HTTPS in production');
    }
    if (new URL(publicBaseUrl).protocol !== 'https:') {
      throw new Error('VITE_PUBLIC_BASE_URL must use HTTPS in production');
    }
  }
  cachedEnv = {
    apiBaseUrl,
    publicBaseUrl,
    appEnv: appEnv as EnvConfig['appEnv'],
    enableVoiceDemo: booleanValue(getEnvVar('VITE_ENABLE_VOICE_DEMO', 'true'), 'VITE_ENABLE_VOICE_DEMO'),
    enableAnalytics: booleanValue(getEnvVar('VITE_ENABLE_ANALYTICS', 'true'), 'VITE_ENABLE_ANALYTICS'),
    version: getEnvVar('VITE_APP_VERSION', '1.0.0').trim(),
  };
  return cachedEnv;
}

export function isProduction(): boolean {
  return getEnv().appEnv === 'production';
}

export function isDevelopment(): boolean {
  return getEnv().appEnv === 'development';
}
