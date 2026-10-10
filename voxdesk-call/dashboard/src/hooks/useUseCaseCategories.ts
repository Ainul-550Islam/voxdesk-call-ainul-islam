/**
 * dashboard/src/hooks/useUseCaseCategories.ts
 * Categories fetch with retry, validation, real backend only.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { getUseCaseCategories } from '../api/use-cases';
import type { UseCaseCategory } from '../types/use-case';

interface UseCategoriesReturn {
  categories: UseCaseCategory[];
  loading: boolean;
  error: string | null;
  retry: () => void;
  total: number;
}

export function useUseCaseCategories(): UseCategoriesReturn {
  const [categories, setCategories] = useState<UseCaseCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const retryCountRef = useRef(0);
  const maxRetries = 3;

  const fetchCategories = useCallback(async () => {
    setLoading(true);
    setError(null);
    
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await getUseCaseCategories(controller.signal);
      if (res.status === 'ok' && res.data) {
        const cats = res.data;
        const withAll = cats.some(c => c.id === 'all') ? cats : [{ id: 'all', title: 'All Use Cases', slug: 'all', description: 'Browse all' } as UseCaseCategory, ...cats];
        setCategories(withAll);
        retryCountRef.current = 0;
      } else {
        throw new Error('Failed to load categories');
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      if (retryCountRef.current < maxRetries && e?.message?.includes('network')) {
        retryCountRef.current++;
        setTimeout(() => fetchCategories(), 1000 * retryCountRef.current);
        return;
      }
      setError(e?.message || 'Failed to load categories');
      setCategories([{ id: 'all', title: 'All Use Cases', slug: 'all' } as UseCaseCategory]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCategories();
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [fetchCategories]);

  const retry = useCallback(() => {
    retryCountRef.current = 0;
    fetchCategories();
  }, [fetchCategories]);

  return {
    categories,
    loading,
    error,
    retry,
    total: categories.length,
  };
}

export const USE_CATEGORIES_VERSION = '1.0.0';
