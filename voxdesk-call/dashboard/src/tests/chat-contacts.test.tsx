import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { apiClient } from '../api/client';
import {
  listChatAgents,
  updateChatAgentDraft,
  publishChatAgent,
  createChatSession,
  sendChatMessage,
} from '../api/chat-agents';
import {
  listContacts,
  createContact,
  saveContactMemoryEntry,
} from '../api/contacts';
import { ChatAgentsPage } from '../pages/chat/ChatAgentsPage';
import { ContactsPage } from '../pages/contacts/ContactsPage';

describe('Prompt 2 — Chat Agents, Messages, Contacts & Contact Memory Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('sends If-Match ETag header on updateChatAgentDraft and publishes versions', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValueOnce({
      id: 'ca-1',
      tenant_id: 't-1',
      name: 'Support Concierge',
      description: 'Updated',
      status: 'draft',
      draft_config: { system_prompt: 'Updated prompt', model: 'gpt-4o-mini' },
      draft_version: 2,
      published_version: null,
      etag: 'W/"etag-v2"',
      created_at: '2026-10-03T00:00:00Z',
      updated_at: '2026-10-03T00:01:00Z',
    });

    const updated = await updateChatAgentDraft(
      'ca-1',
      { description: 'Updated' },
      'W/"etag-v1"'
    );
    expect(patchSpy).toHaveBeenCalledWith(
      '/api/chat-agents/ca-1',
      { description: 'Updated' },
      { headers: { 'If-Match': 'W/"etag-v1"' } }
    );
    expect(updated.etag).toBe('W/"etag-v2"');

    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      id: 'cav-1',
      tenant_id: 't-1',
      chat_agent_id: 'ca-1',
      version: 1,
      status: 'published',
      config: { system_prompt: 'Updated prompt' },
      config_hash: 'abc12345',
      change_summary: 'v1 release',
      created_at: '2026-10-03T00:02:00Z',
      published_at: '2026-10-03T00:02:00Z',
    });

    const ver = await publishChatAgent('ca-1', 'v1 release');
    expect(postSpy).toHaveBeenCalledWith('/api/chat-agents/ca-1/publish', {
      change_summary: 'v1 release',
    });
    expect(ver.version).toBe(1);
  });

  it('creates durable chat session and sends message with memory updates', async () => {
    vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({
        id: 'sess-1',
        tenant_id: 't-1',
        chat_agent_id: 'ca-1',
        chat_agent_version: 1,
        contact_id: 'c-1',
        channel: 'web',
        status: 'active',
        dynamic_variables: { tier: 'pro' },
        metadata: {},
        message_count: 1,
        started_at: '2026-10-03T00:00:00Z',
        created_at: '2026-10-03T00:00:00Z',
        updated_at: '2026-10-03T00:00:00Z',
      })
      .mockResolvedValueOnce({
        session: {
          id: 'sess-1',
          tenant_id: 't-1',
          chat_agent_id: 'ca-1',
          channel: 'web',
          status: 'active',
          dynamic_variables: {},
          metadata: {},
          message_count: 3,
          started_at: '2026-10-03T00:00:00Z',
          created_at: '2026-10-03T00:00:00Z',
          updated_at: '2026-10-03T00:01:00Z',
        },
        user_message: {
          id: 'm-2',
          tenant_id: 't-1',
          session_id: 'sess-1',
          sequence: 2,
          role: 'user',
          content: 'Check my billing plan',
          tool_calls: [],
          metadata: {},
          created_at: '2026-10-03T00:01:00Z',
        },
        assistant_message: {
          id: 'm-3',
          tenant_id: 't-1',
          session_id: 'sess-1',
          sequence: 3,
          role: 'assistant',
          content: '[Support Concierge] Memory[preferred_language=Bengali]',
          tool_calls: [],
          metadata: {},
          created_at: '2026-10-03T00:01:00Z',
        },
        memory_keys_used: ['preferred_language'],
        memory_keys_saved: ['preferred_language'],
      });

    const sess = await createChatSession('ca-1', {
      contact_phone: '+14155550199',
      contact_name: 'Alice Rahman',
      channel: 'web',
    });
    expect(sess.id).toBe('sess-1');

    const turn = await sendChatMessage('sess-1', {
      content: 'Check my billing plan',
      memory_updates: { preferred_language: 'Bengali' },
    });
    expect(turn.assistant_message.sequence).toBe(3);
    expect(turn.memory_keys_saved).toContain('preferred_language');
  });

  it('creates contacts and saves contact memory entries via API client', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      id: 'c-1',
      tenant_id: 't-1',
      phone: '+14155550199',
      phone_raw: '415-555-0199',
      name: 'Alice Rahman',
      email: 'alice@example.com',
      company: 'Acme Ltd',
      custom_fields: {},
      source: 'manual',
      lifecycle: 'active',
      created_at: '2026-10-03T00:00:00Z',
      updated_at: '2026-10-03T00:00:00Z',
    });
    vi.spyOn(apiClient, 'put').mockResolvedValueOnce({
      id: 'mem-1',
      tenant_id: 't-1',
      contact_id: 'c-1',
      key: 'preferred_language',
      value: 'Bengali',
      value_type: 'string',
      source: 'manual',
      importance: 90,
      created_at: '2026-10-03T00:00:00Z',
      updated_at: '2026-10-03T00:00:00Z',
    });

    const contact = await createContact({
      phone: '415-555-0199',
      name: 'Alice Rahman',
    });
    expect(contact.phone).toBe('+14155550199');

    const mem = await saveContactMemoryEntry('c-1', 'preferred_language', {
      value: 'Bengali',
      importance: 90,
    });
    expect(mem.key).toBe('preferred_language');
    expect(mem.value).toBe('Bengali');
  });

  it('renders ChatAgentsPage with durable chat agents and version history', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async (path: string) => {
      if (path.startsWith('/api/chat-agents') && !path.includes('/versions')) {
        return [
          {
            id: 'ca-1',
            tenant_id: 't-1',
            name: 'Enterprise Billing Concierge',
            description: 'Handles billing chat',
            status: 'published',
            draft_config: {
              system_prompt: 'You are Billing Concierge.',
              first_message: 'Hello!',
              model: 'gpt-4o-mini',
              temperature: 0.2,
            },
            published_config: {
              system_prompt: 'You are Billing Concierge.',
              first_message: 'Hello!',
              model: 'gpt-4o-mini',
              temperature: 0.2,
            },
            draft_version: 2,
            published_version: 1,
            etag: 'W/"etag-ca-1"',
            created_at: '2026-10-03T00:00:00Z',
            updated_at: '2026-10-03T00:00:00Z',
          },
        ] as any;
      }
      if (path.includes('/versions')) {
        return [
          {
            id: 'cav-1',
            tenant_id: 't-1',
            chat_agent_id: 'ca-1',
            version: 1,
            status: 'published',
            config: { system_prompt: 'You are Billing Concierge.' },
            config_hash: '1122334455667788',
            change_summary: 'Initial production release',
            created_at: '2026-10-03T00:00:00Z',
            published_at: '2026-10-03T00:00:00Z',
          },
        ] as any;
      }
      return [] as any;
    });

    render(<ChatAgentsPage />);
    await waitFor(() => {
      expect(
        screen.getAllByText('Enterprise Billing Concierge').length
      ).toBeGreaterThan(0);
      expect(
        screen.getByText(/Initial production release/i)
      ).toBeInTheDocument();
    });
  });

  it('renders ContactsPage with durable contacts and memory entries', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async (path: string) => {
      if (path.startsWith('/api/contacts') && !path.includes('/memory')) {
        return [
          {
            id: 'c-1',
            tenant_id: 't-1',
            phone: '+14155550199',
            phone_raw: '+14155550199',
            name: 'Nusrat Jahan',
            email: 'nusrat@example.com',
            company: 'Dhaka Cloud Ltd',
            custom_fields: {},
            source: 'crm',
            lifecycle: 'active',
            created_at: '2026-10-03T00:00:00Z',
            updated_at: '2026-10-03T00:00:00Z',
          },
        ] as any;
      }
      if (path.includes('/memory')) {
        return [
          {
            id: 'mem-1',
            tenant_id: 't-1',
            contact_id: 'c-1',
            key: 'preferred_language',
            value: 'Prefers Bengali support',
            value_type: 'string',
            source: 'manual',
            importance: 90,
            created_at: '2026-10-03T00:00:00Z',
            updated_at: '2026-10-03T00:00:00Z',
          },
        ] as any;
      }
      return [] as any;
    });

    render(<ContactsPage />);
    await waitFor(() => {
      expect(screen.getAllByText('Nusrat Jahan').length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getByText('preferred_language')).toBeInTheDocument();
      expect(screen.getByText('Prefers Bengali support')).toBeInTheDocument();
    });
  });
});
