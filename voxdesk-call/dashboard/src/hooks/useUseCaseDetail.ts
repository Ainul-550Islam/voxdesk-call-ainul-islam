/**
 * dashboard/src/hooks/useUseCaseDetail.ts
 * Detail state machine: LOADING/READY/NOT_FOUND/ERROR/NOT_CONFIGURED, slug regex validation.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { getUseCaseBySlug } from '../api/use-cases';
import type { UseCaseDetail, UseCaseLoadingState } from '../types/use-case';

const SLUG_REGEX = /^[a-z0-9-]+$/;
const MAX_SLUG_LENGTH = 200;

interface UseUseCaseDetailReturn {
  data: UseCaseDetail | null;
  state: UseCaseLoadingState;
  error: string | null;
  loading: boolean;
  isNotFound: boolean;
  isError: boolean;
  isReady: boolean;
  isNotConfigured: boolean;
  retry: () => void;
  slug: string;
}

export function useUseCaseDetail(slug: string): UseUseCaseDetailReturn {
  const [data, setData] = useState<UseCaseDetail | null>(null);
  const [state, setState] = useState<UseCaseLoadingState>('loading');
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);

  const validateSlug = useCallback((s: string): boolean => {
    if (!s || typeof s !== 'string') return false;
    if (s.length === 0 || s.length > MAX_SLUG_LENGTH) return false;
    return SLUG_REGEX.test(s);
  }, []);

  const fetchDetail = useCallback(async (currentSlug: string) => {
    if (!validateSlug(currentSlug)) {
      setState('not-found');
      setError('Invalid use case slug');
      setData(null);
      return;
    }

    const requestId = ++requestIdRef.current;
    setState('loading');
    setError(null);

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await getUseCaseBySlug(currentSlug, controller.signal);
      if (requestId !== requestIdRef.current) return;

      if (res.status === 'ok' && res.data) {
        const detail = res.data;
        if (detail.supported === false) {
          setState('not-configured');
          setData(detail);
        } else if (!detail.workflow || detail.workflow.length === 0) {
          setState('not-configured');
          setData(detail);
        } else {
          setState('ready');
          setData(detail);
        }
      } else {
        setState('not-found');
        setData(null);
        setError('Use case not found');
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      if (requestId !== requestIdRef.current) return;
      
      const msg = e?.message || 'Failed to load';
      if (msg.toLowerCase().includes('not found') || e?.status === 404) {
        setState('not-found');
        setError('Use case not found');
      } else {
        setState('error');
        setError(msg);
      }
      setData(null);
    }
  }, [validateSlug]);

  useEffect(() => {
    fetchDetail(slug);
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [slug, fetchDetail]);

  const retry = useCallback(() => {
    fetchDetail(slug);
  }, [slug, fetchDetail]);

  return {
    data,
    state,
    error,
    loading: state === 'loading',
    isNotFound: state === 'not-found',
    isError: state === 'error',
    isReady: state === 'ready',
    isNotConfigured: state === 'not-configured',
    retry,
    slug,
  };
}

export const USE_USE_CASE_DETAIL_VERSION = '1.0.0';
