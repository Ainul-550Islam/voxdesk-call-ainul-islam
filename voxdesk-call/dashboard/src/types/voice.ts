export type VoiceState='IDLE'|'LISTENING'|'PROCESSING'|'SPEAKING'|'INTERRUPTED'|'ERROR'|'NOT_CONFIGURED';
export interface VoiceDemoSession { status:'ok'; session_id:string; expires_at:string; transport:string; configuration:{ session_id:string; expires_at:string; transport:string; ice_servers:{urls:string[]}[]; voice_states:VoiceState[]; initial_state:VoiceState; provider_configured:boolean; message:string; }; }
export interface VoiceDemoEvent { event:'start'|'stop'|'interrupt'|'error'; timestamp:string; data?:Record<string,unknown>; }
export interface VoiceDemoEventResponse { status:'ok'; session_id:string; event:string; new_state:VoiceState; at:string; }
export interface VoiceDemoSessionState { status:'ok'; session_id:string; state:VoiceState; created_at:string; expires_at:string; events:{event:string; timestamp:string; new_state:VoiceState; data:Record<string,unknown>}[]; }
export interface UseVoiceDemoReturn { state:VoiceState; session:VoiceDemoSession|null; loading:boolean; error:string|null; start:()=>Promise<void>; stop:()=>Promise<void>; interrupt:()=>Promise<void>; isConfigured:boolean; }
