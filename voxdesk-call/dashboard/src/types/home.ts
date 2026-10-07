export type HomeEvidenceStatus = 'PARTIAL' | 'MISSING';

export interface Capability {
  id: string;
  title: string;
  description: string;
  icon: string;
  href: string;
  category: string;
  status: HomeEvidenceStatus;
  evidence_routes: string[];
}

export interface UseCase {
  id: string;
  title: string;
  description: string;
  icon: string;
  href: string;
  status: HomeEvidenceStatus;
  evidence_routes: string[];
}

export interface SecurityItem {
  id: string;
  title: string;
  description: string;
  status: HomeEvidenceStatus;
  evidence_routes: string[];
}

export interface DeveloperFeature {
  id: string;
  title: string;
  description: string;
  docs_href: string;
  status: HomeEvidenceStatus;
  evidence_routes: string[];
}

export interface HomeData {
  capabilities: Capability[];
  use_cases: UseCase[];
  security_items: SecurityItem[];
  developer_features: DeveloperFeature[];
}

export interface HomeResponse {
  status: 'ok';
  data: HomeData;
  meta: {
    generated_at: string;
    registered_api_operations: number;
    evidence_scope: string;
  };
}

export interface AnalyticsSummary {
  calls: number | null;
  successful_calls: number | null;
  average_duration_seconds: number | null;
  average_latency_ms: number | null;
  cost: number | null;
  status: 'ok' | 'empty' | 'not_configured' | 'error';
  message: string | null;
}

export interface AnalyticsSummaryResponse {
  status: 'ok';
  data: AnalyticsSummary;
}

export type HomeLoadingState = 'loading' | 'loaded' | 'empty' | 'error' | 'not_configured';
