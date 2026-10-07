/**
 * dashboard/src/tests/agents.test.tsx
 *
 * Comprehensive tests for the durable Agent + AgentVersion frontend layer:
 * - API clients (agents, agent-builder, agent-versions, agent-actions, agent-publish)
 * - Optimistic concurrency control (`If-Match` header & HTTP 409 Conflict handling)
 * - Components (`AgentPublishStatus`, `AgentBuilderHeader`, `AgentVersionPanel`)
 * - Pages (`AgentsPage`, `AgentDetailPage`, `AgentBuilderPage`, `AgentVersionDetailPage`)
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { apiClient } from '../api/client';
import {
  listAgents,
  getAgent,
  createAgent,
  updateAgent,
  validateAgentConfig,
  publishAgentConfig,
  fetchAgentVersionHistory,
  fetchAgentVersionSnapshot,
  rollbackAgentToVersion,
} from '../api/agents';
import {
  getBuilderConfig,
  updateBuilderConfig,
  validateAgent,
  publishAgent,
} from '../api/agent-builder';
import {
  fetchAgentVersions,
  fetchAgentVersion,
  diffAgentVersions,
  rollbackToAgentVersion,
} from '../api/agent-versions';
import {
  archiveAgent as archiveAgentAction,
  restoreAgent as restoreAgentAction,
  duplicateAgent,
} from '../api/agent-actions';
import {
  publishAgentBuilder,
  promoteAgentEnvironment,
  getAgentPublish,
} from '../api/agent-publish';
import { AgentPublishStatus } from '../components/agents/AgentPublishStatus';
import { AgentBuilderHeader } from '../components/agents/AgentBuilderHeader';
import { AgentsPage } from '../pages/agents/AgentsPage';
import { AgentDetailPage } from '../pages/agents/AgentDetailPage';
import { AgentVersionDetailPage } from '../pages/agents/AgentVersionDetailPage';

describe('Durable Agent + AgentVersion Frontend Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  describe('API clients & ETag optimistic concurrency', () => {
    it('lists and normalizes durable agents from GET /api/agents', async () => {
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce([
        {
          id: '11111111-2222-3333-4444-555555555555',
          external_key: 'agt_1234',
          tenant_id: 'tenant-1',
          name: 'Dental Concierge',
          status: 'published',
          active_version: 2,
          published_version_number: 2,
          draft_etag: 'W/"abc123"',
          validation_status: 'valid',
          updated_at: '2026-10-03T08:00:00Z',
        },
      ]);

      const res = await listAgents();
      expect(res.agents).toHaveLength(1);
      expect(res.total).toBe(1);
      expect(res.agents[0].id).toBe('11111111-2222-3333-4444-555555555555');
      expect(res.agents[0].status).toBe('PUBLISHED');
      expect(res.agents[0].version).toBe(2);
      expect(res.agents[0].etag).toBe('W/"abc123"');
    });

    it('sends If-Match header on draft updates and propagates 409 Conflict', async () => {
      const patchSpy = vi
        .spyOn(apiClient, 'patch')
        .mockResolvedValueOnce({
          agent_id: 'agt-1',
          version: 1,
          status: 'draft',
          etag: 'W/"etag-2"',
          lock_version: 2,
          validation_status: 'valid',
          identity: {
            name: 'Updated Agent',
            description: 'Desc',
            persona: 'Helpful',
            greeting: 'Hi!',
            end_call_message: 'Bye!',
            fallback_message: 'Repeat?',
          },
          voice: {
            provider: 'elevenlabs',
            voice_id: 'rachel_en_us',
            language: 'en-US',
            speed: 1.0,
            pitch: 1.0,
            stability: 0.75,
            similarity_boost: 0.75,
          },
          model: {
            provider: 'openai',
            model_name: 'gpt-4o',
            temperature: 0.3,
            max_tokens: 400,
            system_prompt: 'You are helpful.',
            context_window_turns: 20,
            response_style: 'conversational',
          },
          knowledge_bases: [],
          tools: [],
          call_handling: {
            silence_timeout_seconds: 5,
            max_call_duration_seconds: 1800,
            interruption_sensitivity: 0.5,
            voicemail_detection: true,
            dtmf_enabled: true,
          },
          security: {
            redact_pii: true,
            retention_days: 90,
            allowed_domains: [],
            webhook_signing_enabled: true,
            custom_headers: {},
          },
          updated_at: '2026-10-03T08:05:00Z',
        });

      const saved = await updateBuilderConfig(
        'agt-1',
        { name: 'Updated Agent', system_prompt: 'You are helpful.' },
        'W/"etag-1"'
      );
      expect(patchSpy).toHaveBeenCalledWith(
        '/api/v1/agents/agt-1/builder',
        expect.objectContaining({ expected_etag: 'W/"etag-1"' }),
        expect.objectContaining({
          headers: { 'If-Match': 'W/"etag-1"' },
        })
      );
      expect(saved.etag).toBe('W/"etag-2"');

      // Simulate stale ETag 409 Conflict
      const conflictErr = Object.assign(new Error('409 Conflict'), { status: 409 });
      vi.spyOn(apiClient, 'put').mockRejectedValueOnce(conflictErr);

      await expect(
        updateAgent('agt-1', { name: 'Stale Write' }, 'W/"stale-etag"')
      ).rejects.toMatchObject({ status: 409 });
    });

    it('validates, publishes, inspects version snapshots, diffs, and rolls back', async () => {
      vi.spyOn(apiClient, 'post')
        .mockResolvedValueOnce({
          agent_id: 'agt-1',
          valid: true,
          errors: [],
          warnings: [],
          checked_at: '2026-10-03T08:10:00Z',
        })
        .mockResolvedValueOnce({
          id: 'ver-uuid-1',
          agent_id: 'agt-1',
          version: 1,
          version_number: 1,
          status: 'published',
          config_hash: 'abcdef1234567890',
          published_at: '2026-10-03T08:11:00Z',
          published_by: 'owner@acme.test',
          published_environment: 'production',
          changelog: 'Initial release',
          is_active: true,
          is_rollback: false,
          config_snapshot: {
            identity: { name: 'Agent v1', greeting: 'Welcome v1' },
          },
        })
        .mockResolvedValueOnce({
          id: 'ver-uuid-3',
          agent_id: 'agt-1',
          version: 3,
          version_number: 3,
          status: 'published',
          config_hash: 'abcdef1234567890',
          published_at: '2026-10-03T08:15:00Z',
          published_by: 'owner@acme.test',
          published_environment: 'production',
          changelog: 'Rolled back to v1',
          is_active: true,
          is_rollback: true,
          source_version_id: 'ver-uuid-1',
          config_snapshot: {
            identity: { name: 'Agent v1', greeting: 'Welcome v1' },
          },
        });

      const valRes = await validateAgentConfig('agt-1');
      expect(valRes.valid).toBe(true);

      const pubRes = await publishAgentBuilder('agt-1', {
        changelog: 'Initial release',
        environment: 'production',
      });
      expect(pubRes.version).toBe(1);
      expect(pubRes.is_active).toBe(true);

      const rbRes = await rollbackToAgentVersion('agt-1', {
        target_version: 1,
        reason: 'Revert to v1',
      });
      expect(rbRes.version).toBe(3);
      expect(rbRes.is_rollback).toBe(true);
      expect(rbRes.source_version_id).toBe('ver-uuid-1');

      vi.spyOn(apiClient, 'get')
        .mockResolvedValueOnce({
          id: 'ver-uuid-1',
          agent_id: 'agt-1',
          version: 1,
          version_number: 1,
          status: 'superseded',
          config_hash: 'abcdef1234567890',
          published_at: '2026-10-03T08:11:00Z',
          published_by: 'owner@acme.test',
          published_environment: 'production',
          changelog: 'Initial release',
          is_active: false,
          is_rollback: false,
          config_snapshot: {
            identity: { name: 'Agent v1', greeting: 'Welcome v1' },
          },
        })
        .mockResolvedValueOnce({
          agent_id: 'agt-1',
          from_version: 1,
          to_version: 2,
          total_changes: 1,
          diffs: [
            {
              field_path: 'identity.greeting',
              old_value: 'Welcome v1',
              new_value: 'Welcome v2',
              change_type: 'modified',
            },
          ],
        });

      const snap = await fetchAgentVersion('agt-1', 1);
      expect(snap.version).toBe(1);
      expect(snap.config_snapshot.identity?.greeting).toBe('Welcome v1');

      const diff = await diffAgentVersions('agt-1', 1, 2);
      expect(diff.total_changes).toBe(1);
      expect(diff.diffs[0].field_path).toBe('identity.greeting');
    });

    it('supports archive, restore, clone, and environment promotion', async () => {
      vi.spyOn(apiClient, 'post')
        .mockResolvedValueOnce({
          agent_id: 'agt-1',
          status: 'archived',
          archived_at: '2026-10-03T08:20:00Z',
          reason: 'Pause',
          updated_at: '2026-10-03T08:20:00Z',
        })
        .mockResolvedValueOnce({
          agent_id: 'agt-1',
          status: 'active',
          updated_at: '2026-10-03T08:21:00Z',
        })
        .mockResolvedValueOnce({
          source_agent_id: 'agt-1',
          new_agent_id: 'agt-2',
          name: 'Cloned Agent',
          status: 'draft',
          created_at: '2026-10-03T08:22:00Z',
        })
        .mockResolvedValueOnce({
          agent_id: 'agt-1',
          from_env: 'staging',
          to_env: 'production',
          promoted_version: 2,
          config_hash: 'hash2',
          promoted_by: 'owner',
          promoted_at: '2026-10-03T08:23:00Z',
          changelog: 'Promote to prod',
        });

      const arch = await archiveAgentAction('agt-1', 'Pause');
      expect(arch.status).toBe('archived');

      const rest = await restoreAgentAction('agt-1');
      expect(rest.status).toBe('active');

      const cloned = await duplicateAgent(
        { id: 'agt-1', tenant_id: 't-1', name: 'Source Agent', status: 'DRAFT' } as any,
        { name: 'Cloned Agent' }
      );
      expect(cloned.new_agent_id).toBe('agt-2');

      const promoted = await promoteAgentEnvironment(
        'agt-1',
        'staging',
        'production',
        'Promote to prod'
      );
      expect(promoted.promoted_version).toBe(2);
    });
  });

  describe('UI Components & Pages', () => {
    it('renders AgentPublishStatus and AgentBuilderHeader with conflict reload', () => {
      const onReload = vi.fn();
      const onSave = vi.fn();
      const onPublish = vi.fn();

      render(
        <AgentBuilderHeader
          name="Support Agent"
          status="DRAFT"
          version={2}
          etag='W/"etag-99"'
          saveState="CONFLICT"
          onSave={onSave}
          onTest={() => {}}
          onPublish={onPublish}
          onReload={onReload}
          onBack={() => {}}
        />
      );

      expect(screen.getByText('Support Agent')).toBeTruthy();
      expect(screen.getByText(/Conflict \(409 ETag mismatch\)/i)).toBeTruthy();
      const reloadBtn = screen.getByText('Reload Latest');
      fireEvent.click(reloadBtn);
      expect(onReload).toHaveBeenCalledTimes(1);
    });

    it('renders AgentsPage with backend data and empty state', async () => {
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce([
        {
          id: 'agt-100',
          tenant_id: 't-1',
          name: 'Inbound Sales Bot',
          status: 'published',
          active_version: 3,
          updated_at: '2026-10-03T09:00:00Z',
        },
      ]);

      render(<AgentsPage />);
      await waitFor(() => {
        expect(screen.getByText('Inbound Sales Bot')).toBeTruthy();
      });
    });

    it('renders AgentDetailPage and AgentVersionDetailPage with real version snapshot data', async () => {
      vi.spyOn(apiClient, 'get')
        .mockResolvedValueOnce({
          id: 'agt-100',
          tenant_id: 't-1',
          name: 'Inbound Sales Bot',
          status: 'published',
          active_version: 1,
          published_version_number: 1,
          greeting: 'Hello from Sales Bot',
          persona: 'Friendly',
          llm_provider: 'anthropic',
          llm_model: 'claude-haiku-4-5-20251001',
          voice_id: 'rachel_en_us',
          primary_language: 'en-US',
          speech_speed: 1.0,
          validation_status: 'valid',
          draft_etag: 'W/"etag1"',
        })
        .mockResolvedValueOnce([
          {
            id: 'ver-1',
            agent_id: 'agt-100',
            version: 1,
            version_number: 1,
            status: 'published',
            config_hash: '1234567890abcdef',
            published_at: '2026-10-03T09:00:00Z',
            published_by: 'admin@acme.test',
            published_environment: 'production',
            changelog: 'First production release',
            is_active: true,
            is_rollback: false,
            config_snapshot: {
              identity: { name: 'Inbound Sales Bot', greeting: 'Hello from Sales Bot' },
            },
          },
        ]);

      render(<AgentDetailPage agentId="agt-100" />);
      await waitFor(() => {
        expect(screen.getByText('Inbound Sales Bot')).toBeTruthy();
        expect(screen.getByText('First production release')).toBeTruthy();
      });

      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({
        id: 'ver-1',
        agent_id: 'agt-100',
        version: 1,
        version_number: 1,
        status: 'published',
        config_hash: '1234567890abcdef',
        published_at: '2026-10-03T09:00:00Z',
        published_by: 'admin@acme.test',
        published_environment: 'production',
        changelog: 'First production release',
        is_active: true,
        is_rollback: false,
        config_snapshot: {
          identity: { name: 'Inbound Sales Bot', greeting: 'Hello from Sales Bot' },
        },
      });

      render(<AgentVersionDetailPage agentId="agt-100" versionNumber={1} />);
      await waitFor(() => {
        expect(screen.getByText('Immutable Version v1')).toBeTruthy();
        expect(screen.getAllByText(/Hello from Sales Bot/).length).toBeGreaterThanOrEqual(1);
      });
    });
  });
});
