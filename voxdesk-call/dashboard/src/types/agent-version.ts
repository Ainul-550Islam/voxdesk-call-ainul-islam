/** dashboard/src/types/agent-version.ts — Durable AgentVersion domain types */
export type {
  AgentVersionStatus,
  AgentPublishEnvironment,
  AgentVersionSnapshot,
  AgentVersionFieldDiff,
  AgentVersionDiffResponse,
  PublishAgentVersionInput,
  RollbackAgentVersionInput,
  PromoteAgentEnvironmentInput,
  PromoteAgentEnvironmentResponse,
  AgentEnvironmentDeploymentStatus,
} from '../api/types/agent-version';

export interface AgentVersionState {
  id: string;
  tenant_id: string;
  version_number?: number;
  status?: string;
  updated_at?: string;
}
