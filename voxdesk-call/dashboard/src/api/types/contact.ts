export type ContactLifecycleStatus = 'active' | 'archived' | 'blocked';
export type ContactSourceKind =
  | 'manual'
  | 'call'
  | 'chat'
  | 'sms'
  | 'crm'
  | 'import'
  | 'api';

export interface ContactRecord {
  id: string;
  tenant_id: string;
  environment_id?: string | null;
  phone: string;
  phone_raw: string;
  name: string;
  email?: string | null;
  company?: string | null;
  custom_fields: Record<string, unknown>;
  source: ContactSourceKind | string;
  lifecycle: ContactLifecycleStatus | string;
  archived_at?: string | null;
  crm_provider?: string | null;
  crm_external_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateContactInput {
  phone: string;
  name?: string;
  email?: string | null;
  company?: string | null;
  custom_fields?: Record<string, unknown>;
  source?: ContactSourceKind;
  environment_id?: string | null;
  crm_provider?: string | null;
  crm_external_id?: string | null;
}

export interface UpdateContactInput {
  name?: string;
  email?: string | null;
  company?: string | null;
  custom_fields?: Record<string, unknown>;
  lifecycle?: ContactLifecycleStatus;
  crm_provider?: string | null;
  crm_external_id?: string | null;
}

export type MemoryValueTypeKind = 'string' | 'number' | 'boolean' | 'json';
export type MemorySourceKind =
  | 'manual'
  | 'call'
  | 'chat'
  | 'crm'
  | 'agent'
  | 'api';

export interface ContactMemoryRecord {
  id: string;
  tenant_id: string;
  contact_id: string;
  key: string;
  value: string;
  value_type: MemoryValueTypeKind | string;
  source: MemorySourceKind | string;
  source_ref?: string | null;
  confidence?: number | null;
  importance: number;
  expires_at?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SaveContactMemoryInput {
  value: string;
  value_type?: MemoryValueTypeKind;
  source?: MemorySourceKind;
  source_ref?: string | null;
  confidence?: number | null;
  importance?: number;
  expires_at?: string | null;
}
