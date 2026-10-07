import { useCallback, useEffect, useState } from 'react';
import { getAnalyticsSummary, getPublicHome } from '../api/home';
import { ApiError } from '../api/client';
import type { AnalyticsSummary, HomeData, HomeLoadingState } from '../types/home';

export interface UseHomeDataReturn {
  homeData: HomeData | null;
  analytics: AnalyticsSummary | null;
  loadingState: HomeLoadingState;
  loading: boolean;
  error: string | null;
  retry: () => void;
  isEmpty: boolean;
  isNotConfigured: boolean;
}

function describeFailure(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'The public home API request failed without a readable error message.';
}

function isNotConfigured(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'NOT_CONFIGURED';
}

export function useHomeData(): UseHomeDataReturn {
  const [homeData, setHomeData] = useState<HomeData | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [loadingState, setLoadingState] = useState<HomeLoadingState>('loading');
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoadingState('loading');
    setError(null);
    const [homeResult, analyticsResult] = await Promise.allSettled([
      getPublicHome(),
      getAnalyticsSummary(),
    ]);

    if (homeResult.status === 'fulfilled' && homeResult.value?.data) {
      setHomeData(homeResult.value.data);
      setLoadingState('loaded');
    } else {
      const failure = homeResult.status === 'rejected'
        ? homeResult.reason
        : new Error('The public home API returned no data.');
      setHomeData(null);
      setError(describeFailure(failure));
      setLoadingState(isNotConfigured(failure) ? 'not_configured' : 'error');
    }

    if (analyticsResult.status === 'fulfilled' && analyticsResult.value?.data) {
      setAnalytics(analyticsResult.value.data);
    } else {
      const failure = analyticsResult.status === 'rejected'
        ? analyticsResult.reason
        : new Error('The analytics API returned no data.');
      setAnalytics({
        calls: null,
        successful_calls: null,
        average_duration_seconds: null,
        average_latency_ms: null,
        cost: null,
        status: 'error',
        message: describeFailure(failure),
      });
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return {
    homeData,
    analytics,
    loadingState,
    loading: loadingState === 'loading',
    error,
    retry: fetchData,
    isEmpty: loadingState === 'empty' || analytics?.status === 'empty',
    isNotConfigured: loadingState === 'not_configured' || analytics?.status === 'not_configured',
  };
}

export default useHomeData;
