import { getEnv } from '../config/env';
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
async function readErrorBody(resp: Response): Promise<unknown> { try { const data=await resp.json(); return (data as any).detail ?? data; } catch { return null; } }
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
export async function apiRequest<T=unknown>(path: string, opts: RequestOptions={}): Promise<T> {
  const env=getEnv(); const url=resolveApiUrl(env.apiBaseUrl,path);
  const method=opts.method||'GET'; const timeoutMs=opts.timeoutMs??DEFAULT_TIMEOUT; const controller=new AbortController(); const timeoutId=setTimeout(()=>controller.abort(), timeoutMs);
  const combinedSignal=opts.signal?(()=>{ const combined=new AbortController(); const onAbort=()=>combined.abort(); opts.signal!.addEventListener('abort',onAbort); controller.signal.addEventListener('abort',onAbort); return combined.signal; })():controller.signal;
  const headers: Record<string,string> = {...(opts.raw?{}:{'Content-Type':'application/json'}), ...(opts.headers||{})};
  const token=opts.authenticated===false?null:(typeof window!=='undefined'?localStorage.getItem('voxdesk_access_token'):null); if (token) headers['Authorization']=`Bearer ${token}`;
  const requestId=`req_${Math.random().toString(36).slice(2,10)}`; headers['X-Request-ID']=requestId;
  let resp: Response;
  try { resp=await fetch(url,{method,headers,credentials:opts.credentials??'same-origin',body:opts.raw?(opts.body as BodyInit):opts.body!==undefined?JSON.stringify(opts.body):undefined,signal:combinedSignal}); }
  catch (e:any) { clearTimeout(timeoutId); if (e.name==='AbortError') throw new ApiError('TIMEOUT',0,'Request timed out.',null,requestId); throw new ApiError('NETWORK_ERROR',0,friendlyMessage(0,null),null,requestId); }
  finally { clearTimeout(timeoutId); }
  const responseRequestId=resp.headers.get('X-Request-ID')||requestId;
  if (!resp.ok) { const detail=await readErrorBody(resp); const code=mapStatusToCode(resp.status,detail); const message=friendlyMessage(resp.status,detail); throw new ApiError(code,resp.status,message,detail,responseRequestId); }
  if (resp.status===204) return null as unknown as T;
  try { const data=await resp.json(); return data as T; } catch { throw new ApiError('UNKNOWN',resp.status,'Invalid response format.',null,responseRequestId); }
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
