import { getEnv } from '../config/env';
import { getAccessToken, refreshAccessToken } from '../lib/session';
export type ApiErrorCode = 'NETWORK_ERROR' | 'TIMEOUT' | 'UNAUTHORIZED' | 'FORBIDDEN' | 'NOT_FOUND' | 'VALIDATION_ERROR' | 'SERVER_ERROR' | 'NOT_CONFIGURED' | 'RATE_LIMITED' | 'UNKNOWN';
export class ApiError extends Error {
  code: ApiErrorCode; status: number; detail: unknown; requestId?: string;
  constructor(code: ApiErrorCode, status: number, message: string, detail: unknown = null, requestId?: string) {
    super(message); this.name='ApiError'; this.code=code; this.status=status; this.detail=detail; this.requestId=requestId;
  }
  get retryable(): boolean { return this.code==='NETWORK_ERROR' || this.code==='TIMEOUT' || this.code==='RATE_LIMITED' || this.status>=500; }
}
function mapStatusToCode(status: number, detail: unknown): ApiErrorCode {
  if (status===0) return 'NETWORK_ERROR'; if (status===401) return 'UNAUTHORIZED'; if (status===403) return 'FORBIDDEN';
  if (status===404) return 'NOT_FOUND'; if (status===422) return 'VALIDATION_ERROR'; if (status===429) return 'RATE_LIMITED';
  if (status>=500) return 'SERVER_ERROR';
  if (detail && typeof detail==='object' && 'message' in (detail as any)) {
    const msg=(detail as any).message as string; if (msg && msg.toLowerCase().includes('not configured')) return 'NOT_CONFIGURED';
  }
  if (typeof detail==='string' && detail.toLowerCase().includes('not configured')) return 'NOT_CONFIGURED';
  return 'UNKNOWN';
}
function friendlyMessage(status: number, detail: unknown): string {
  if (typeof detail==='string' && detail.trim()) return detail;
  if (detail && typeof detail==='object' && 'message' in (detail as any) && typeof (detail as any).message==='string') return (detail as any).message;
  const map: Record<number,string> = {0:'Could not reach server.',400:'Request could not be processed.',401:'Session expired.',403:'No permission.',404:'Not found.',409:'Conflict.',422:'Invalid details.',429:'Too many requests.',502:'Third-party unavailable.',503:'Service unavailable.',504:'Timed out.'};
  return map[status] || 'Something went wrong.';
}
/**
 * The backend's canonical failure body is
 * ``{"error": {"code", "message", "request_id", "detail"?}}``
 * (``app/core/errors.py``). Unwrap it so callers see the *safe* message the
 * server authored instead of degrading to a generic status sentence, and keep
 * the machine code reachable for status-independent classification.
 */
interface BackendErrorEnvelope { code?: unknown; message?: unknown; request_id?: unknown; detail?: unknown; }
async function readErrorBody(resp: Response): Promise<{ detail: unknown; code?: string; requestId?: string }> {
  try {
    const data = await resp.json() as any;
    const envelope = data && typeof data === 'object' ? (data as any).error : null;
    if (envelope && typeof envelope === 'object') {
      const body = envelope as BackendErrorEnvelope;
      return {
        detail: typeof body.detail === 'undefined' ? (body.message ?? envelope) : body.detail,
        code: typeof body.code === 'string' ? body.code : undefined,
        requestId: typeof body.request_id === 'string' ? body.request_id : undefined,
      };
    }
    return { detail: data && typeof data === 'object' && 'detail' in data ? data.detail : data };
  } catch { return { detail: null }; }
}

/** Backend ``error.code`` values that are not a plain HTTP-status translation. */
const CODE_OVERRIDES: Record<string, ApiErrorCode> = {
  not_configured: 'NOT_CONFIGURED',
  rate_limited: 'RATE_LIMITED',
  permission_denied: 'FORBIDDEN',
  not_found: 'NOT_FOUND',
  conflict: 'UNKNOWN',
  bad_request: 'VALIDATION_ERROR',
  service_unavailable: 'SERVER_ERROR',
  internal_error: 'SERVER_ERROR',
};

function classify(status: number, detail: unknown, code?: string): ApiErrorCode {
  if (code && CODE_OVERRIDES[code]) return CODE_OVERRIDES[code];
  return mapStatusToCode(status, detail);
}
export interface RequestOptions { method?: 'GET'|'POST'|'PUT'|'PATCH'|'DELETE'; body?: unknown; headers?: Record<string,string>; timeoutMs?: number; retry?: boolean; raw?: boolean; signal?: AbortSignal; authenticated?: boolean; credentials?: RequestCredentials; }
const DEFAULT_TIMEOUT=15000;
export function resolveApiUrl(base: string, path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  let root = base.replace(/\/$/, '');
  const relative = path.startsWith('/') ? path : `/${path}`;
  if (root.endsWith('/api') && (relative === '/api' || relative.startsWith('/api/'))) {
    root = root.slice(0, -4);
  }
  return `${root}${relative}`;
}
/**
 * One request attempt. ``sessionToken`` overrides the value read from the
 * session store so a replayed request can carry the freshly rotated token.
 */
async function attemptRequest<T>(path: string, opts: RequestOptions, sessionToken?: string|null): Promise<T> {
  const env=getEnv(); const url=resolveApiUrl(env.apiBaseUrl,path);
  const method=opts.method||'GET'; const timeoutMs=opts.timeoutMs??DEFAULT_TIMEOUT; const controller=new AbortController(); const timeoutId=setTimeout(()=>controller.abort(), timeoutMs);
  const combinedSignal=opts.signal?(()=>{ const combined=new AbortController(); const onAbort=()=>combined.abort(); opts.signal!.addEventListener('abort',onAbort); controller.signal.addEventListener('abort',onAbort); return combined.signal; })():controller.signal;
  const headers: Record<string,string> = {...(opts.raw?{}:{'Content-Type':'application/json'}), ...(opts.headers||{})};
  const token=opts.authenticated===false?null:(sessionToken !== undefined ? sessionToken : getAccessToken()); if (token) headers['Authorization']=`Bearer ${token}`;
  const requestId=`req_${Math.random().toString(36).slice(2,10)}`; headers['X-Request-ID']=requestId;
  let resp: Response;
  try { resp=await fetch(url,{method,headers,credentials:opts.credentials??'same-origin',body:opts.raw?(opts.body as BodyInit):opts.body!==undefined?JSON.stringify(opts.body):undefined,signal:combinedSignal}); }
  catch (e:any) { clearTimeout(timeoutId); if (e.name==='AbortError') throw new ApiError('TIMEOUT',0,'Request timed out.',null,requestId); throw new ApiError('NETWORK_ERROR',0,friendlyMessage(0,null),null,requestId); }
  finally { clearTimeout(timeoutId); }
  const responseRequestId=resp.headers.get('X-Request-ID')||requestId;
  if (!resp.ok) { const body=await readErrorBody(resp); const code=classify(resp.status,body.detail,body.code); const message=friendlyMessage(resp.status,body.detail); const error=new ApiError(code,resp.status,message,body.detail,body.requestId||responseRequestId); (error as ApiError & { fromAttempt?: boolean }).fromAttempt=true; throw error; }
  if (resp.status===204) return null as unknown as T;
  try { const data=await resp.json(); return data as T; } catch { throw new ApiError('UNKNOWN',resp.status,'Invalid response format.',null,responseRequestId); }
}
/**
 * Replay an expired bearer token exactly once.
 *
 * A 401 from an authenticated call means the short-lived access token expired,
 * not that the user signed out: the HttpOnly refresh cookie is still valid.
 * ``refreshAccessToken`` is single-flight across concurrent callers, so a burst
 * of parallel requests rotates the cookie once and every request replays with
 * the same new token. When rotation fails the original 401 is surfaced.
 */
async function withSessionRefresh<T>(
  path: string,
  opts: RequestOptions,
  attempt: () => Promise<T>,
): Promise<T> {
  try {
    return await attempt();
  } catch (error) {
    const expired = error instanceof ApiError
      && error.status === 401
      && (error as ApiError & { fromAttempt?: boolean }).fromAttempt === true;
    if (!expired || opts.authenticated === false) throw error;
    const refreshed = await refreshAccessToken();
    if (!refreshed) throw error;
    return attemptRequest<T>(path, opts, refreshed);
  }
}

export async function apiRequest<T=unknown>(path: string, opts: RequestOptions={}): Promise<T> {
  return withSessionRefresh<T>(path, opts, () => attemptRequest<T>(path, opts));
}

export async function apiRequestWithRetry<T>(path: string, opts: RequestOptions={}, maxRetries=2): Promise<T> {
  let lastError: ApiError|null=null;
  for (let attempt=0; attempt<=maxRetries; attempt++) {
    try { return await apiRequest<T>(path,opts); } catch (e) {
      if (e instanceof ApiError) { lastError=e; if ([401,403,400,404,422].includes(e.status)) throw e; if (!e.retryable) throw e; if (attempt===maxRetries) throw e; await new Promise((r)=>setTimeout(r,300*Math.pow(2,attempt))); } else throw e;
    }
  }
  throw lastError!;
}
export const client={ get:<T>(path:string, opts?:Omit<RequestOptions,'method'|'body'>)=>apiRequest<T>(path,{...opts,method:'GET'}), post:<T>(path:string, body?:unknown, opts?:Omit<RequestOptions,'method'|'body'>)=>apiRequest<T>(path,{...opts,method:'POST',body}), put:<T>(path:string, body?:unknown, opts?:Omit<RequestOptions,'method'|'body'>)=>apiRequest<T>(path,{...opts,method:'PUT',body}), patch:<T>(path:string, body?:unknown, opts?:Omit<RequestOptions,'method'|'body'>)=>apiRequest<T>(path,{...opts,method:'PATCH',body}), del:<T>(path:string, opts?:Omit<RequestOptions,'method'|'body'>)=>apiRequest<T>(path,{...opts,method:'DELETE'}), delete:<T>(path:string, opts?:Omit<RequestOptions,'method'|'body'>)=>apiRequest<T>(path,{...opts,method:'DELETE'}), };
// Backward compatibility alias — many modules import apiClient
export const apiClient = client;
// Also export default for convenience
export default client;
