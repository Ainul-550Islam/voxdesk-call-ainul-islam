/** dashboard/src/types/agent-actions.ts — Durable Agent lifecycle action types */
export interface AgentActionState {
  id: string;
  tenant_id: string;
  status?: string;
  updated_at?: string;
}
