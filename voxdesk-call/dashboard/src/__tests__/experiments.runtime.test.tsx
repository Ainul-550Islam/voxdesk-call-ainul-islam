import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { client } from '../api/client';
import { ExperimentsPage } from '../pages/testing/ExperimentsPage';
import { matchRoute } from '../app/router';

describe('A/B Experiments UI & API runtime', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('registers /experiments and /dashboard/experiments in the router', () => {
    expect(matchRoute('/experiments')?.route.component).toBe('ExperimentsPage');
    expect(matchRoute('/dashboard/experiments')?.route.component).toBe('ExperimentsPage');
  });

  it('lists experiments, creates a new experiment, starts, promotes, and rolls back', async () => {
    const expDraft = {
      id: 'exp-101',
      tenant_id: 'ten-1',
      name: 'Greeting Test',
      description: 'Test greeting conciseness',
      agent_id: '11111111-1111-1111-1111-111111111111',
      status: 'draft',
      traffic_split: {},
      success_metric: 'conversion_rate',
      min_sample_size: 30,
      confidence_level: 0.95,
      winner_variant_id: null,
      variants: [
        {
          id: 'var-ctrl',
          name: 'Control',
          agent_version_id: null,
          weight: 50,
          is_control: true,
          config_overrides: {},
          impressions: 50,
          conversions: 10,
          conversion_rate: 0.2,
        },
        {
          id: 'var-b',
          name: 'Variant B',
          agent_version_id: null,
          weight: 50,
          is_control: false,
          config_overrides: { greeting: 'Hi!' },
          impressions: 50,
          conversions: 22,
          conversion_rate: 0.44,
        },
      ],
      started_at: null,
      ended_at: null,
      promoted_at: null,
      created_at: '2026-10-29T00:00:00Z',
      updated_at: '2026-10-29T00:00:00Z',
    };

    const resultsPayload = {
      experiment_id: 'exp-101',
      status: 'running',
      total_impressions: 100,
      min_sample_size: 30,
      sample_size_reached: true,
      confidence_level: 0.95,
      variants: [
        {
          variant_id: 'var-ctrl',
          name: 'Control',
          is_control: true,
          impressions: 50,
          conversions: 10,
          conversion_rate: 0.2,
          ci_lower: 0.1,
          ci_upper: 0.33,
          z_score: null,
          p_value: null,
          significant: null,
          lift_pct: null,
        },
        {
          variant_id: 'var-b',
          name: 'Variant B',
          is_control: false,
          impressions: 50,
          conversions: 22,
          conversion_rate: 0.44,
          ci_lower: 0.31,
          ci_upper: 0.58,
          z_score: 2.57,
          p_value: 0.0101,
          significant: true,
          lift_pct: 120.0,
        },
      ],
      recommended_winner_variant_id: 'var-b',
    };

    let currentStatus = 'draft';
    vi.spyOn(client, 'get').mockImplementation(async (path: string) => {
      if (path.endsWith('/results')) {
        return { ...resultsPayload, status: currentStatus } as any;
      }
      return {
        experiments: [{ ...expDraft, status: currentStatus }],
        total: 1,
        limit: 50,
        offset: 0,
      } as any;
    });

    const postSpy = vi.spyOn(client, 'post').mockImplementation(async (path: string) => {
      if (path.endsWith('/start')) {
        currentStatus = 'running';
        return { ...expDraft, status: 'running' } as any;
      }
      if (path.endsWith('/promote')) {
        currentStatus = 'promoted';
        return {
          experiment_id: 'exp-101',
          status: 'promoted',
          winner_variant_id: 'var-b',
          agent_id: expDraft.agent_id,
          previous_version_id: 'ver-1',
          previous_version_number: 1,
          promoted_version_id: 'ver-2',
          promoted_version_number: 2,
          promoted_at: '2026-10-29T00:05:00Z',
        } as any;
      }
      if (path.endsWith('/rollback')) {
        currentStatus = 'rolled_back';
        return {
          experiment_id: 'exp-101',
          status: 'rolled_back',
          agent_id: expDraft.agent_id,
          rolled_back_from_version_id: 'ver-2',
          restored_version_id: 'ver-1',
          restored_version_number: 1,
          rolled_back_at: '2026-10-29T00:06:00Z',
        } as any;
      }
      return expDraft as any;
    });

    render(<ExperimentsPage />);

    await waitFor(() => {
      expect(screen.getAllByText('Greeting Test').length).toBeGreaterThan(0);
    });
    expect(await screen.findByText('0.0101')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        '/api/ab-testing/experiments/exp-101/start',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: 'Promote Variant B' }));
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        '/api/ab-testing/experiments/exp-101/promote',
        { variant_id: 'var-b' },
      );
    });

    const rollbackBtn = await screen.findByRole('button', { name: 'Rollback Promotion' });
    fireEvent.click(rollbackBtn);
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        '/api/ab-testing/experiments/exp-101/rollback',
        {},
      );
    });
  });
});
