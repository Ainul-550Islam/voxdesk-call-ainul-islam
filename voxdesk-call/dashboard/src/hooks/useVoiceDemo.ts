import { useState, useCallback, useRef, useEffect } from 'react';
import { createVoiceDemoSession, postVoiceDemoEvent, getVoiceDemoSession } from '../api/voice-demo';
import type { VoiceState, VoiceDemoSession, UseVoiceDemoReturn } from '../types/voice';
import { ApiError } from '../api/client';
export function useVoiceDemo(tenantId?: string): UseVoiceDemoReturn {
  const [state,setState]=useState<VoiceState>('IDLE');
  const [session,setSession]=useState<VoiceDemoSession|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const sessionRef=useRef<VoiceDemoSession|null>(null);
  useEffect(()=>{ sessionRef.current=session; },[session]);
  const ensureSession=useCallback(async():Promise<VoiceDemoSession>=>{
    if (sessionRef.current) { const expiresAt=new Date(sessionRef.current.expires_at); if (expiresAt>new Date()) return sessionRef.current; }
    const newSession=await createVoiceDemoSession(tenantId); setSession(newSession);
    if (!newSession.configuration.provider_configured) setState('NOT_CONFIGURED'); else setState(newSession.configuration.initial_state as VoiceState);
    return newSession;
  },[tenantId]);
  const sendEvent=useCallback(async(event:'start'|'stop'|'interrupt'|'error', data:Record<string,unknown>={})=>{
    const sess=await ensureSession();
    try { const res=await postVoiceDemoEvent(sess.session_id,{event,timestamp:new Date().toISOString(),data}); setState(res.new_state); return res; }
    catch (e) { const apiErr=e as ApiError; if (apiErr.status===404) { setSession(null); const newSess=await createVoiceDemoSession(tenantId); setSession(newSess); const res=await postVoiceDemoEvent(newSess.session_id,{event,timestamp:new Date().toISOString(),data}); setState(res.new_state); return res; } throw e; }
  },[ensureSession,tenantId]);
  const start=useCallback(async()=>{ setLoading(true); setError(null); try { await sendEvent('start'); } catch (e) { const apiErr=e as ApiError; setError(apiErr.message); setState('ERROR'); } finally { setLoading(false); } },[sendEvent]);
  const stop=useCallback(async()=>{ setLoading(true); setError(null); try { await sendEvent('stop'); } catch (e) { const apiErr=e as ApiError; setError(apiErr.message); setState('ERROR'); } finally { setLoading(false); } },[sendEvent]);
  const interrupt=useCallback(async()=>{ setLoading(true); setError(null); try { await sendEvent('interrupt'); } catch (e) { const apiErr=e as ApiError; setError(apiErr.message); setState('ERROR'); } finally { setLoading(false); } },[sendEvent]);
  useEffect(()=>{
    if (!session || state==='IDLE' || state==='NOT_CONFIGURED' || state==='ERROR') return;
    const interval=setInterval(async()=>{ try { const sessState=await getVoiceDemoSession(session.session_id); if (sessState.state!==state) setState(sessState.state); } catch {} },2000);
    return ()=>clearInterval(interval);
  },[session,state]);
  const isConfigured=session?.configuration.provider_configured??false;
  return {state,session,loading,error,start,stop,interrupt,isConfigured};
}
