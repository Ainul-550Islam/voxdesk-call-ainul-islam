/**
 * dashboard/src/__tests__/agentStudio.pages.test.tsx
 *
 * Tests for the Agent Studio surfaces that used to be placeholders:
 *
 *   AgentListPage, AgentKnowledgePage, AgentVoicePage, AgentModelPage,
 *   AgentToolsPage, AgentTestHistoryPage, AgentArchivePage, AgentDuplicatePage
 *
 * together with the client-side list helpers (search / filter / sort) and the
 * router registrations that make those pages reachable.
 *
 * `../api/client` is mocked, so these exercise the real hooks and the real
 * components against controlled responses. Nothing here asserts on invented
 * data: every fixture below mirrors the shape the matching backend route
 * returns.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { apiClient } from '../api/client';
import { searchAgents } from '../api/agent-search';
import { applyAgentFilters, availableAgentFilterValues } from '../api/agent-filters';
import { sortAgents } from '../api/agent-sort';
import { ROUTES } from '../app/router';

import { AgentListPage } from '../pages/agents/AgentListPage';
import { AgentKnowledgePage } from '../pages/agents/AgentKnowledgePage';
import { AgentVoicePage } from '../pages/agents/AgentVoicePage';
import { AgentModelPage } from '../pages/agents/AgentModelPage';
import { AgentToolsPage } from '../pages/agents/AgentToolsPage';
import { AgentTestHistoryPage } from '../pages/agents/AgentTestHistoryPage';
import { AgentArchivePage } from '../pages/agents/AgentArchivePage';
import { AgentDuplicatePage } from '../pages/agents/AgentDuplicatePage';

vi.mock('../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  client: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    code: string;
    status: number;
    constructor(code: string, status: number, message: string) {
      super(message);
      this.code = code;
      this.status = status;
    }
  },
}));

const AGENT_ID = '11111111-1111-1111-1111-111111111111';

const AGENTS = [
  {
    id: AGENT_ID,
    tenant_id: 'tenant-1',
    name: 'Inbound Receptionist',
    description: 'Answers clinic calls and books appointments',
    status: 'PUBLISHED',
    type: 'VOICE',
    language: 'en-US',
    version: 3,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
  },
  {
    id: '22222222-2222-2222-2222-222222222222',
    tenant_id: 'tenant-1',
    name: 'Outbound Qualifier',
    description: 'Qualifies inbound leads',
    status: 'DRAFT',
    type: 'VOICE',
    language: 'en-GB',
    version: 1,
    created_at: '2026-09-05T10:00:00Z',
    updated_at: '2026-10-03T10:00:00Z',
  },
  {
    id: '33333333-3333-3333-3333-333333333333',
    tenant_id: 'tenant-1',
    name: 'Billing Assistant',
    description: '',
    status: 'ARCHIVED',
    type: 'CHAT',
    language: 'en-US',
    version: 2,
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
  },
];

const VOICE_CATALOG = {
  providers: [
    {
      id: 'elevenlabs',
      label: 'ElevenLabs',
      allowed_by_domain: true,
      configured: true,
      installed: true,
      contract_declared: true,
      buildable: true,
      selectable: true,
      distribution: 'pipecat-ai',
      sdk_version: '0.0.94',
      capabilities: ['supports_speed'],
      runtime_probe: 'not_performed',
      reachable: 'not_checked',
      authenticated: 'not_checked',
      reason: null,
    },
    {
      id: 'google',
      label: 'Google Cloud TTS',
      allowed_by_domain: true,
      configured: false,
      installed: true,
      contract_declared: true,
      buildable: true,
      selectable: false,
      distribution: 'google-genai',
      sdk_version: '1.0.0',
      capabilities: [],
      runtime_probe: 'not_performed',
      reachable: 'not_checked',
      authenticated: 'not_checked',
      reason: 'no google API key configured in this deployment',
    },
  ],
  voices: [
    {
      voice_id: '21m00Tcm4TlvDq8ikWAM',
      provider: 'elevenlabs',
      source: 'deployment_settings',
      label: 'Configured ElevenLabs voice',
    },
  ],
  default_provider: 'elevenlabs',
  default_voice_id: '21m00Tcm4TlvDq8ikWAM',
  voice_library_fetched: false,
  voice_library_note:
    'Provider voice libraries are not queried by this endpoint. Only voices configured in this deployment are listed.',
  generated_at: '2026-10-04T00:00:00Z',
  notes: [],
};

const MODEL_CATALOG = {
  providers: [
    {
      id: 'openai',
      label: 'OpenAI',
      allowed_by_domain: true,
      configured: true,
      installed: true,
      contract_declared: true,
      buildable: true,
      selectable: true,
      distribution: 'openai',
      sdk_version: '1.0.0',
      capabilities: ['supports_tool_calling'],
      runtime_probe: 'not_performed',
      reachable: 'not_checked',
      authenticated: 'not_checked',
      reason: null,
    },
  ],
  presets: [
    {
      preset: 'fast',
      provider: 'openai',
      model: 'gpt-4o-mini',
      est_latency_ms: 250,
      notes: 'fastest',
      configured: true,
      installed: true,
      selectable: true,
    },
    {
      preset: 'natural',
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
      est_latency_ms: 300,
      notes: 'most human',
      configured: false,
      installed: true,
      selectable: false,
    },
  ],
  default_provider: 'openai',
  default_provider_source: 'first_selectable_preset',
  default_preset: 'fast',
  configured_stt_model: 'nova-3',
  configured_tts_model: 'eleven_flash_v2_5',
  generated_at: '2026-10-04T00:00:00Z',
  notes: [],
};

const BUILDER_DRAFT = {
  agent_id: AGENT_ID,
  id: AGENT_ID,
  external_key: 'agent-1',
  tenant_id: 'tenant-1',
  environment_id: null,
  status: 'draft',
  version: 3,
  published_version: null,
  published_version_id: null,
  etag: 'etag-abc',
  draft_etag: 'etag-abc',
  lock_version: 1,
  validation_status: 'unvalidated',
  updated_at: '2026-10-01T10:00:00Z',
  created_at: '2026-09-01T10:00:00Z',
  identity: { name: 'Inbound Receptionist' },
  voice: {
    provider: 'elevenlabs',
    voice_id: '21m00Tcm4TlvDq8ikWAM',
    language: 'en-US',
    speed: 1,
    pitch: 1,
    stability: 0.75,
    similarity_boost: 0.75,
    fallback_voice_id: null,
  },
  model: {
    provider: 'openai',
    model_name: 'gpt-4o-mini',
    temperature: 0.3,
    max_tokens: 512,
    system_prompt: 'You are a receptionist.',
    context_window_turns: 20,
    response_style: 'conversational',
  },
  knowledge_bases: [],
  tools: [],
  call_handling: {},
  security: {},
};

function routeFor(path: string) {
  const get = vi.mocked(apiClient.get);
  return async (url: string) => {
    if (url.startsWith('/api/agents/voices')) return VOICE_CATALOG;
    if (url.startsWith('/api/agents/models')) return MODEL_CATALOG;
    if (url.startsWith('/api/agents?include_archived=true')) return AGENTS;
    if (url === '/api/agents' || url.startsWith('/api/agents?')) return AGENTS;
    if (url === `/api/agents/${AGENT_ID}/tools`) return TOOLS;
    if (url.startsWith('/api/agents/')) return AGENTS[0];
    if (url.startsWith('/api/knowledge/documents')) return KNOWLEDGE_LIST;
    if (url.startsWith('/api/knowledge/stats')) return { documents: 1 };
    if (url.startsWith('/api/v1/testing/runs')) return TEST_RUNS;
    if (url.startsWith('/api/v1/agents/') && url.endsWith('/builder')) return BUILDER_DRAFT;
    return [];
  };
}

const KNOWLEDGE_LIST = {
  documents: [
    {
      id: 'doc-1',
      title: 'Clinic pricing',
      status: 'ready',
      source_type: 'upload',
      original_filename: 'pricing.pdf',
      file_type: 'application/pdf',
      file_size: 20480,
      version: 1,
      chunk_count: 12,
      char_count: 9000,
      token_estimate: 2200,
      embedding_model: 'hashing-v1',
      embedding_dimensions: 384,
      error_message: null,
      metadata: {},
      created_at: '2026-10-01T10:00:00Z',
      updated_at: '2026-10-01T10:00:00Z',
      indexed_at: '2026-10-01T10:05:00Z',
      is_searchable: true,
    },
    {
      id: 'doc-2',
      title: 'Broken upload',
      status: 'failed',
      source_type: 'upload',
      original_filename: 'broken.pdf',
      file_type: 'application/pdf',
      file_size: 100,
      version: 1,
      chunk_count: 0,
      char_count: 0,
      token_estimate: 0,
      embedding_model: null,
      embedding_dimensions: null,
      error_message: 'Unsupported file type',
      metadata: {},
      created_at: '2026-10-02T10:00:00Z',
      updated_at: '2026-10-02T10:00:00Z',
      indexed_at: null,
      is_searchable: false,
    },
  ],
  total: 2,
  limits: {},
};

const TOOLS = [
  {
    id: 'tool-1',
    tenant_id: 'tenant-1',
    agent_id: AGENT_ID,
    name: 'check_order_status',
    description: 'Look up an order by id',
    schema: { endpoint_url: 'https://api.example.com/orders', http_method: 'POST' },
    auth_binding: {},
    is_enabled: true,
  },
  {
    id: 'tool-2',
    tenant_id: 'tenant-1',
    agent_id: AGENT_ID,
    name: 'cancel_order',
    description: '',
    schema: {},
    auth_binding: {},
    is_enabled: false,
  },
];

const TEST_RUNS = [
  {
    id: 'run-aaaaaaaaaaaa',
    tenant_id: 'tenant-1',
    agent_id: AGENT_ID,
    agent_kind: 'voice',
    agent_version_number: 3,
    agent_config_hash: 'abc123def456',
    mode: 'multi_turn',
    status: 'passed',
    is_mock_provider: false,
    provider: 'openai',
    model: 'gpt-4o-mini',
    correlation_id: 'corr-1',
    transcript_snapshot: [],
    events_snapshot: [],
    usage_metadata: {},
    scorecard_summary: {},
    evaluation_results: [],
    created_at: '2026-10-02T10:00:00Z',
    updated_at: '2026-10-02T10:00:00Z',
  },
  {
    id: 'run-bbbbbbbbbbbb',
    tenant_id: 'tenant-1',
    agent_id: AGENT_ID,
    agent_kind: 'voice',
    agent_version_number: 3,
    agent_config_hash: 'abc123def456',
    mode: 'single_turn',
    status: 'failed',
    is_mock_provider: true,
    provider: 'openai',
    model: 'gpt-4o-mini',
    correlation_id: 'corr-2',
    transcript_snapshot: [],
    events_snapshot: [],
    usage_metadata: {},
    scorecard_summary: {},
    evaluation_results: [],
    created_at: '2026-10-03T10:00:00Z',
    updated_at: '2026-10-03T10:00:00Z',
  },
];

beforeEach(() => {
  vi.mocked(apiClient.get).mockImplementation(routeFor('get') as never);
  vi.mocked(apiClient.post).mockResolvedValue({} as never);
  vi.mocked(apiClient.patch).mockResolvedValue(BUILDER_DRAFT as never);
  vi.mocked(apiClient.delete).mockResolvedValue(undefined as never);
});

// ---------------------------------------------------------------- pages ----

describe('AgentListPage', () => {
  it('lists agents returned by the API', async () => {
    render(<AgentListPage />);
    await waitFor(() => expect(screen.getByText('Inbound Receptionist')).toBeTruthy());
    expect(screen.getByText('Outbound Qualifier')).toBeTruthy();
    expect(screen.getByText('Billing Assistant')).toBeTruthy();
    expect(screen.getByText(/Showing 3 of 3 agents/)).toBeTruthy();
  });

  it('filters rows by search text', async () => {
    render(<AgentListPage />);
    await waitFor(() => expect(screen.getByText('Inbound Receptionist')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Search agents'), { target: { value: 'billing' } });
    await waitFor(() => expect(screen.queryByText('Inbound Receptionist')).toBeNull());
    expect(screen.getByText('Billing Assistant')).toBeTruthy();
  });

  it('filters rows by status', async () => {
    render(<AgentListPage />);
    await waitFor(() => expect(screen.getByText('Inbound Receptionist')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'ARCHIVED' } });
    await waitFor(() => expect(screen.queryByText('Inbound Receptionist')).toBeNull());
    expect(screen.getByText('Billing Assistant')).toBeTruthy();
  });

  it('shows an explicit empty state when nothing matches', async () => {
    render(<AgentListPage />);
    await waitFor(() => expect(screen.getByText('Inbound Receptionist')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Search agents'), { target: { value: 'zzzz-no-match' } });
    await waitFor(() => expect(screen.getByText('No agents match')).toBeTruthy());
  });
});

describe('AgentKnowledgePage', () => {
  it('renders documents and surfaces server-side ingestion errors', async () => {
    render(<AgentKnowledgePage />);
    await waitFor(() => expect(screen.getByText('Clinic pricing')).toBeTruthy());
    expect(screen.getByText('Unsupported file type')).toBeTruthy();
    expect(screen.getByText('ready')).toBeTruthy();
  });

  it('counts only real rows', async () => {
    render(<AgentKnowledgePage />);
    await waitFor(() => expect(screen.getByText('Clinic pricing')).toBeTruthy());
    expect(screen.getByText('2')).toBeTruthy();
    expect(screen.getByText(/Retrieval preview/)).toBeTruthy();
  });
});

describe('AgentVoicePage', () => {
  it('offers selectable providers and explains unavailable ones', async () => {
    render(<AgentVoicePage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByText('ElevenLabs — ready')).toBeTruthy());
    expect(
      screen.getByText('Google Cloud TTS — no google API key configured in this deployment'),
    ).toBeTruthy();
    expect(screen.getByText(/Configuration is not connectivity/)).toBeTruthy();
  });

  it('lists only voices configured in this deployment', async () => {
    render(<AgentVoicePage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByText('21m00Tcm4TlvDq8ikWAM')).toBeTruthy());
    expect(screen.getByText(/Provider voice libraries are not queried/)).toBeTruthy();
  });

  it('saves voice settings through the builder with the draft ETag', async () => {
    render(<AgentVoicePage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByLabelText('Voice ID')).toBeTruthy());
    const button = await screen.findByText('Save voice settings');
    fireEvent.click(button);
    await waitFor(() => expect(vi.mocked(apiClient.patch)).toHaveBeenCalled());
    const [url, , options] = vi.mocked(apiClient.patch).mock.calls[0] as [string, unknown, { headers: Record<string, string> }];
    expect(url).toBe(`/api/v1/agents/${AGENT_ID}/builder`);
    expect(options?.headers?.['If-Match']).toBe('etag-abc');
  });
});

describe('AgentModelPage', () => {
  it('renders runtime presets and marks unavailable ones', async () => {
    render(<AgentModelPage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByText(/fast · gpt-4o-mini/)).toBeTruthy());
    expect(screen.getByText(/natural · claude-haiku-4-5 \(unavailable\)/)).toBeTruthy();
    expect(screen.getByText('nova-3')).toBeTruthy();
    expect(screen.getByText('eleven_flash_v2_5')).toBeTruthy();
  });

  it('saves model settings through the builder', async () => {
    render(<AgentModelPage agentId={AGENT_ID} />);
    const button = await screen.findByText('Save model settings');
    fireEvent.click(button);
    await waitFor(() => expect(vi.mocked(apiClient.patch)).toHaveBeenCalled());
    const [url] = vi.mocked(apiClient.patch).mock.calls[0];
    expect(url).toBe(`/api/v1/agents/${AGENT_ID}/builder`);
  });
});

describe('AgentToolsPage', () => {
  it('renders registered tools with their enabled state', async () => {
    render(<AgentToolsPage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByText('check_order_status')).toBeTruthy());
    expect(screen.getByText('cancel_order')).toBeTruthy();
    expect(screen.getAllByText('enabled').length).toBeGreaterThan(0);
    expect(screen.getAllByText('disabled').length).toBeGreaterThan(0);
  });

  it('rejects an invalid tool name instead of sending it', async () => {
    render(<AgentToolsPage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByLabelText(/Name \(used by the model\)/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/Name \(used by the model\)/), {
      target: { value: 'not a valid name' },
    });
    fireEvent.click(screen.getByText('Register tool'));
    await waitFor(() =>
      expect(screen.getByText(/Tool names must start with a letter or underscore/)).toBeTruthy(),
    );
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('toggles a tool through the enable/disable endpoints', async () => {
    render(<AgentToolsPage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByText('cancel_order')).toBeTruthy());
    const row = screen.getByText('cancel_order').closest('li') as HTMLElement;
    fireEvent.click(within(row).getByText('Enable'));
    await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalled());
    const [url] = vi.mocked(apiClient.post).mock.calls[0];
    expect(url).toBe(`/api/agents/${AGENT_ID}/tools/tool-2/enable`);
  });
});

describe('AgentTestHistoryPage', () => {
  it('lists persisted runs scoped to the agent', async () => {
    render(<AgentTestHistoryPage agentId={AGENT_ID} />);
    await waitFor(() => expect(screen.getByText('passed')).toBeTruthy());
    expect(screen.getByText('failed')).toBeTruthy();
    expect(screen.getByText('mock provider')).toBeTruthy();
    const [url] = vi.mocked(apiClient.get).mock.calls.find((call) =>
      String(call[0]).startsWith('/api/v1/testing/runs'),
    ) as [string];
    expect(url).toContain(`agent_id=${AGENT_ID}`);
  });

  it('states that past browser sessions are not listable', async () => {
    render(<AgentTestHistoryPage agentId={AGENT_ID} />);
    // Wait for the persisted-run request to settle so the assertion below is
    // not racing an in-flight state update.
    await waitFor(() => expect(screen.getByText('passed')).toBeTruthy());
    expect(
      screen.getByText(/The runtime exposes no endpoint that lists past browser sessions/),
    ).toBeTruthy();
  });
});

describe('AgentArchivePage', () => {
  it('lists only rows the server reports as archived', async () => {
    render(<AgentArchivePage />);
    await waitFor(() => expect(screen.getByText('Billing Assistant')).toBeTruthy());
    expect(screen.queryByText('Inbound Receptionist')).toBeNull();
    expect(screen.queryByText('Outbound Qualifier')).toBeNull();
  });

  it('restores through the lifecycle endpoint', async () => {
    render(<AgentArchivePage />);
    await waitFor(() => expect(screen.getByText('Billing Assistant')).toBeTruthy());
    fireEvent.click(screen.getByText('Restore'));
    await waitFor(() => expect(vi.mocked(apiClient.post)).toHaveBeenCalled());
    const [url] = vi.mocked(apiClient.post).mock.calls[0];
    expect(url).toBe('/api/v1/agents/33333333-3333-3333-3333-333333333333/restore');
  });
});

describe('AgentDuplicatePage', () => {
  it('prefills the copy name from the source agent', async () => {
    render(<AgentDuplicatePage agentId={AGENT_ID} />);
    await waitFor(() =>
      expect((screen.getByLabelText('Name for the copy') as HTMLInputElement).value).toBe(
        'Inbound Receptionist (copy)',
      ),
    );
  });

  it('clones through the lifecycle endpoint and links to the copy', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      new_agent_id: '44444444-4444-4444-4444-444444444444',
      name: 'Inbound Receptionist (copy)',
    } as never);
    render(<AgentDuplicatePage agentId={AGENT_ID} />);
    // The page heading and the submit button share the same text, so target
    // the button by role rather than by label.
    const submit = await screen.findByRole('button', { name: 'Duplicate agent' });
    fireEvent.click(submit);
    await waitFor(() => expect(screen.getByText('Copy created')).toBeTruthy());
    const [url, body] = vi.mocked(apiClient.post).mock.calls[0];
    expect(url).toBe(`/api/v1/agents/${AGENT_ID}/clone`);
    expect((body as Record<string, unknown>).new_name).toBe('Inbound Receptionist (copy)');
  });
});

// ------------------------------------------------------- list utilities ----

describe('client-side list utilities', () => {
  it('searchAgents matches name, description, and id', () => {
    expect(searchAgents(AGENTS as never, 'billing').map((a) => a.name)).toEqual([
      'Billing Assistant',
    ]);
    expect(searchAgents(AGENTS as never, 'OUTBOUND').map((a) => a.name)).toEqual([
      'Outbound Qualifier',
    ]);
    expect(searchAgents(AGENTS as never, AGENT_ID).map((a) => a.name)).toEqual([
      'Inbound Receptionist',
    ]);
    expect(searchAgents(AGENTS as never, '   ')).toHaveLength(3);
  });

  it('applyAgentFilters treats "all" as no filter', () => {
    expect(applyAgentFilters(AGENTS as never, { status: 'all', agentType: 'all', language: 'all' })).toHaveLength(3);
    expect(
      applyAgentFilters(AGENTS as never, {
        status: 'all',
        agentType: 'CHAT',
        language: 'all',
      }).map((a) => a.name),
    ).toEqual(['Billing Assistant']);
    expect(
      applyAgentFilters(AGENTS as never, {
        status: 'all',
        agentType: 'all',
        language: 'en-GB',
      }).map((a) => a.name),
    ).toEqual(['Outbound Qualifier']);
  });

  it('availableAgentFilterValues derives options from the rows', () => {
    const options = availableAgentFilterValues(AGENTS as never);
    expect(options.statuses).toEqual(['ARCHIVED', 'DRAFT', 'PUBLISHED']);
    expect(options.agentTypes).toEqual(['CHAT', 'VOICE']);
    expect(options.languages).toEqual(['en-GB', 'en-US']);
  });

  it('sortAgents sorts missing values last in both directions', () => {
    const rows = [
      { id: 'a', name: 'Alpha', updated_at: '2026-01-02', status: 'DRAFT', version: 1 },
      { id: 'b', name: 'Beta', updated_at: '2026-01-01', status: 'DRAFT', version: 2 },
      { id: 'c', name: 'Gamma', updated_at: undefined, status: 'DRAFT', version: 3 },
    ] as never;
    expect(sortAgents(rows, { key: 'updated_at', direction: 'desc' }).map((r) => r.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
    expect(sortAgents(rows, { key: 'updated_at', direction: 'asc' }).map((r) => r.id)).toEqual([
      'b',
      'a',
      'c',
    ]);
  });

  it('sortAgents does not mutate the input array', () => {
    const rows = [
      { id: 'a', name: 'Zeta', updated_at: '2026-01-01', status: 'DRAFT', version: 1 },
      { id: 'b', name: 'Alpha', updated_at: '2026-01-02', status: 'DRAFT', version: 2 },
    ] as never;
    const snapshot = [...rows];
    sortAgents(rows, { key: 'name', direction: 'asc' });
    expect(rows).toEqual(snapshot);
  });
});

// ------------------------------------------------------------ routing ------

describe('Agent Studio routing', () => {
  const expected: Array<[string, string]> = [
    ['/dashboard/agents/list', 'AgentListPage'],
    ['/dashboard/knowledge', 'AgentKnowledgePage'],
    ['/dashboard/agents/archive', 'AgentArchivePage'],
    ['/dashboard/agents/:id/voice', 'AgentVoicePage'],
    ['/dashboard/agents/:id/model', 'AgentModelPage'],
    ['/dashboard/agents/:id/tools', 'AgentToolsPage'],
    ['/dashboard/agents/:id/test-history', 'AgentTestHistoryPage'],
    ['/dashboard/agents/:id/duplicate', 'AgentDuplicatePage'],
  ];

  it.each(expected)('registers %s -> %s', (path, component) => {
    const route = ROUTES.find((entry) => entry.path === path);
    expect(route, `missing route ${path}`).toBeTruthy();
    expect(route?.component).toBe(component);
  });

  it('marks the archive route exact so it wins over /dashboard/agents/:id', () => {
    const archive = ROUTES.find((entry) => entry.path === '/dashboard/agents/archive');
    const detail = ROUTES.find((entry) => entry.path === '/dashboard/agents/:id');
    expect(archive?.exact).toBe(true);
    expect(detail?.exact).toBe(false);
  });
});
