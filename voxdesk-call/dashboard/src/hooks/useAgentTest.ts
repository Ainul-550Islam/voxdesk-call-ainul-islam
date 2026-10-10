import { useState, useEffect, useCallback, useRef } from 'react';
import { createTestSession, postTestEvent, getTestSession } from '../api/agent-test';
import type { TestSession, TestState } from '../types/agent-test';
export function useAgentTest(agentId: string){
  const [session,setSession]=useState<TestSession|null>(null);
  const [state,setState]=useState<TestState>('IDLE');
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [isConfigured,setIsConfigured]=useState(true);
  const pollRef=useRef<number|undefined>();
  const ensureSession=useCallback(async()=>{ if(session && new Date(session.expires_at||'').getTime() > Date.now()) return session; setLoading(true); try{ const s=await createTestSession(agentId); if(s.state==='NOT_CONFIGURED'){ setIsConfigured(false); setState('NOT_CONFIGURED'); } else { setSession(s); setState(s.state); setIsConfigured(true); } return s; } catch(e:any){ setError(e?.message||'Failed to create test session'); setState('ERROR'); return null; } finally{ setLoading(false);} },[agentId,session]);
  const start=useCallback(async()=>{ const s=await ensureSession(); if(!s||s.state==='NOT_CONFIGURED') return; setState('CONNECTING'); try{ const updated=await postTestEvent(s.id,'start'); setSession(updated); setState(updated.state); } catch(e:any){ setError(e?.message||'Start failed'); setState('ERROR'); } },[ensureSession]);
  const stop=useCallback(async()=>{ if(!session) return; try{ const updated=await postTestEvent(session.id,'stop'); setSession(updated); setState(updated.state); } catch(e:any){ setError(e?.message||'Stop failed'); } },[session]);
  const sendText=useCallback(async(text:string)=>{ if(!session) return; setState('THINKING'); try{ const updated=await postTestEvent(session.id,'text',{ text }); setSession(updated); setState(updated.state); } catch(e:any){ setError(e?.message||'Send failed'); setState('ERROR'); } },[session]);
  useEffect(()=>{ if(state==='LISTENING'||state==='THINKING'||state==='SPEAKING'){ pollRef.current=window.setInterval(async()=>{ if(!session) return; try{ const s=await getTestSession(session.id); setSession(s); setState(s.state); } catch{} },2000); } return ()=>{ if(pollRef.current) clearInterval(pollRef.current); }; },[state,session]);
  return { session, state, loading, error, isConfigured, ensureSession, start, stop, sendText };
}
