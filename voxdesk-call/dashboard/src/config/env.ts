export interface EnvConfig { apiBaseUrl:string; publicBaseUrl:string; appEnv:'development'|'staging'|'production'; enableVoiceDemo:boolean; enableAnalytics:boolean; version:string; }
function getEnvVar(key:string, fallback:string): string { const viteEnv=(import.meta as any).env?.[key]; if (viteEnv) return viteEnv; if (typeof window!=='undefined' && (window as any).__ENV__?.[key]) return (window as any).__ENV__[key]; return fallback; }
let cachedEnv:EnvConfig|null=null;
export function getEnv(): EnvConfig {
  if (cachedEnv) return cachedEnv;
  const apiBaseUrl=getEnvVar('VITE_API_BASE_URL','/api'); const publicBaseUrl=getEnvVar('VITE_PUBLIC_BASE_URL',window.location.origin);
  const appEnv=getEnvVar('VITE_APP_ENV','development') as EnvConfig['appEnv']; const enableVoiceDemo=getEnvVar('VITE_ENABLE_VOICE_DEMO','true')==='true';
  const enableAnalytics=getEnvVar('VITE_ENABLE_ANALYTICS','true')==='true'; const version=getEnvVar('VITE_APP_VERSION','1.0.0');
  if (apiBaseUrl.includes('sk-') || apiBaseUrl.includes('secret')) console.warn('API base URL should not contain secrets');
  cachedEnv={apiBaseUrl,publicBaseUrl,appEnv,enableVoiceDemo,enableAnalytics,version}; return cachedEnv;
}
export function isProduction(): boolean { return getEnv().appEnv==='production'; }
export function isDevelopment(): boolean { return getEnv().appEnv==='development'; }
