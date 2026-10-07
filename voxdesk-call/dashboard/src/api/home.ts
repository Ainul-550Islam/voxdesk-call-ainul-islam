import { client } from './client';
import type { AnalyticsSummaryResponse, HomeResponse } from '../types/home';

export async function getPublicHome(): Promise<HomeResponse> {
  return client.get('/v1/public/home');
}

export async function getAnalyticsSummary(): Promise<AnalyticsSummaryResponse> {
  return client.get('/v1/public/analytics/summary');
}

export async function getPublicHealth(): Promise<Record<string, unknown>> {
  return client.get('/v1/public/health');
}
