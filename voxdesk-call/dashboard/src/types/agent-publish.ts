/** dashboard/src/types/agent-publish.ts — Durable Agent publish & environment types */
export interface AgentPublishState {
  id: string;
  tenant_id: string;
  environment?: string;
  published_version?: number | null;
  updated_at?: string;
}
