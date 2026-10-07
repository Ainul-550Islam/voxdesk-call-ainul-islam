import { apiClient } from './client';
import type {
  ContactMemoryRecord,
  ContactRecord,
  CreateContactInput,
  SaveContactMemoryInput,
  UpdateContactInput,
} from './types/contact';

export async function listContacts(params?: {
  lifecycle?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<ContactRecord[]> {
  const qs = new URLSearchParams();
  if (params?.lifecycle && params.lifecycle !== 'all') {
    qs.set('lifecycle', params.lifecycle);
  }
  if (params?.search) qs.set('search', params.search);
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.offset) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const res = await apiClient.get<ContactRecord[]>(`/api/contacts${suffix}`);
  return Array.isArray(res) ? res : [];
}

export async function getContact(contactId: string): Promise<ContactRecord> {
  return apiClient.get<ContactRecord>(
    `/api/contacts/${encodeURIComponent(contactId)}`
  );
}

export async function createContact(
  input: CreateContactInput
): Promise<ContactRecord> {
  return apiClient.post<ContactRecord>('/api/contacts', {
    phone: input.phone,
    name: input.name || '',
    email: input.email || null,
    company: input.company || null,
    custom_fields: input.custom_fields || {},
    source: input.source || 'manual',
    environment_id: input.environment_id || null,
    crm_provider: input.crm_provider || null,
    crm_external_id: input.crm_external_id || null,
  });
}

export async function updateContact(
  contactId: string,
  input: UpdateContactInput
): Promise<ContactRecord> {
  return apiClient.patch<ContactRecord>(
    `/api/contacts/${encodeURIComponent(contactId)}`,
    input
  );
}

export async function archiveContact(contactId: string): Promise<ContactRecord> {
  return apiClient.post<ContactRecord>(
    `/api/contacts/${encodeURIComponent(contactId)}/archive`,
    {}
  );
}

export async function restoreContact(contactId: string): Promise<ContactRecord> {
  return apiClient.post<ContactRecord>(
    `/api/contacts/${encodeURIComponent(contactId)}/restore`,
    {}
  );
}

export async function deleteContact(contactId: string): Promise<void> {
  await apiClient.delete(`/api/contacts/${encodeURIComponent(contactId)}`);
}

// ----------------------------------------------------------- Contact Memory

export async function listContactMemory(
  contactId: string,
  includeExpired = false
): Promise<ContactMemoryRecord[]> {
  const qs = includeExpired ? '?include_expired=true' : '';
  const res = await apiClient.get<ContactMemoryRecord[]>(
    `/api/contacts/${encodeURIComponent(contactId)}/memory${qs}`
  );
  return Array.isArray(res) ? res : [];
}

export async function getContactMemoryEntry(
  contactId: string,
  key: string
): Promise<ContactMemoryRecord> {
  return apiClient.get<ContactMemoryRecord>(
    `/api/contacts/${encodeURIComponent(contactId)}/memory/${encodeURIComponent(
      key
    )}`
  );
}

export async function saveContactMemoryEntry(
  contactId: string,
  key: string,
  input: SaveContactMemoryInput
): Promise<ContactMemoryRecord> {
  return apiClient.put<ContactMemoryRecord>(
    `/api/contacts/${encodeURIComponent(contactId)}/memory/${encodeURIComponent(
      key
    )}`,
    {
      value: input.value,
      value_type: input.value_type || 'string',
      source: input.source || 'manual',
      source_ref: input.source_ref || null,
      confidence: input.confidence ?? null,
      importance: input.importance ?? 50,
      expires_at: input.expires_at || null,
    }
  );
}

export async function deleteContactMemoryEntry(
  contactId: string,
  key: string
): Promise<void> {
  await apiClient.delete(
    `/api/contacts/${encodeURIComponent(contactId)}/memory/${encodeURIComponent(
      key
    )}`
  );
}
