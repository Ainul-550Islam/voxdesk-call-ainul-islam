/**
 * dashboard/src/hooks/useUseCases.ts
 * Debounced search 300ms, AbortController, requestId race protection, URL sync, pagination, real backend only.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { getUseCases } from '../api/use-cases';
import type { UseCaseSummary, UseCaseCategory, UseCaseListData, UseCaseSearchParams } from '../types/use-case';
import { parseQueryState, buildQueryString, sanitizeQuery } from '../types/use-case-filter';

const DEBOUNCE_MS = 300;
const DEFAULT_PAGE_SIZE = 12;
const MAX_SEARCH_LENGTH = 200;

interface UseUseCasesReturn {
  items: UseCaseSummary[];
  categories: UseCaseCategory[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  search: string;
  category: string;
  setSearch: (q: string) => void;
  setCategory: (cat: string) => void;
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  clearSearch: () => void;
  clearFilters: () => void;
  retry: () => void;
  hasActiveFilters: boolean;
  resultCountLabel: string;
}

export function useUseCases(initialSearch = ''): UseUseCasesReturn {
  const initialParams = useMemo(() => parseQueryState(initialSearch || (typeof window !== 'undefined' ? window.location.search : '')), [initialSearch]);
  
  const [items, setItems] = useState<UseCaseSummary[]>([]);
  const [categories, setCategories] = useState<UseCaseCategory[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPageState] = useState(initialParams.page || 1);
  const [pageSize, setPageSizeState] = useState(initialParams.page_size || DEFAULT_PAGE_SIZE);
  const [search, setSearchState] = useState(initialParams.q || '');
  const [category, setCategoryState] = useState(initialParams.category || 'all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const debounceRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const searchInputRef = useRef(search);

  const totalPages = useMemo(() => Math.ceil(total / pageSize), [total, pageSize]);
  const hasMore = useMemo(() => page * pageSize < total, [page, pageSize, total]);
  const hasActiveFilters = useMemo(() => !!(search || (category && category !== 'all')), [search, category]);
  const resultCountLabel = useMemo(() => {
    if (total === 0) return search ? `No results for "${search}"` : 'No use cases';
    if (total === 1) return search ? `1 result for "${search}"` : '1 use case';
    return search ? `${total} results for "${search}"` : `${total} use cases`;
  }, [total, search]);

  const syncUrl = useCallback((newSearch: string, newCategory: string, newPage: number, newPageSize: number) => {
    if (typeof window === 'undefined') return;
    const params: UseCaseSearchParams = {
      q: newSearch || undefined,
      category: newCategory !== 'all' ? newCategory : undefined,
      page: newPage > 1 ? newPage : undefined,
      page_size: newPageSize !== DEFAULT_PAGE_SIZE ? newPageSize : undefined,
    };
    const qs = buildQueryString(params);
    const newUrl = qs ? `${window.location.pathname}${qs}` : window.location.pathname;
    window.history.replaceState(null, '', newUrl);
  }, []);

  const fetchData = useCallback(async (q: string, cat: string, p: number, ps: number, signal?: AbortSignal) => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    
    try {
      const params: UseCaseSearchParams = {
        q: q ? q.slice(0, MAX_SEARCH_LENGTH) : undefined,
        category: cat !== 'all' ? cat : undefined,
        page: p,
        page_size: ps,
      };
      const res = await getUseCases(params, signal);
      
      if (requestId !== requestIdRef.current) return; // Stale request
      
      if (res.status === 'ok' && res.data) {
        setItems(res.data.items || []);
        setCategories(res.data.categories || []);
        setTotal(res.data.total || 0);
      } else {
        setError('Failed to load use cases');
        setItems([]);
        setTotal(0);
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return; // Aborted, ignore
      if (requestId !== requestIdRef.current) return;
      setError(e?.message || 'Failed to load use cases');
      setItems([]);
      setTotal(0);
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, []);

  const debouncedFetch = useCallback((q: string, cat: string, p: number, ps: number) => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    if (abortRef.current) abortRef.current.abort();
    
    const controller = new AbortController();
    abortRef.current = controller;
    
    debounceRef.current = window.setTimeout(() => {
      fetchData(q, cat, p, ps, controller.signal);
    }, DEBOUNCE_MS);
  }, [fetchData]);

  const immediateFetch = useCallback((q: string, cat: string, p: number, ps: number) => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    if (abortRef.current) abortRef.current.abort();
    
    const controller = new AbortController();
    abortRef.current = controller;
    fetchData(q, cat, p, ps, controller.signal);
  }, [fetchData]);

  // Initial load
  useEffect(() => {
    immediateFetch(search, category, page, pageSize);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, []); // Only on mount

  // Search effect with debounce
  useEffect(() => {
    if (searchInputRef.current === search) return; // Skip initial
    searchInputRef.current = search;
    debouncedFetch(search, category, page, pageSize);
    syncUrl(search, category, page, pageSize);
  }, [search, category, page, pageSize, debouncedFetch, syncUrl]);

  // Category and pagination effect - immediate
  useEffect(() => {
    // Skip if this is caused by search change (already handled by debouncedFetch)
    // We need to track previous values to avoid double fetch
  }, [category, page, pageSize]);

  const setSearch = useCallback((q: string) => {
    const sanitized = sanitizeQuery(q);
    setSearchState(sanitized);
    setPageState(1); // Reset to first page on search
  }, []);

  const setCategory = useCallback((cat: string) => {
    const validCat = cat && /^[a-z0-9-]+$/.test(cat) ? cat : 'all';
    setCategoryState(validCat);
    setPageState(1);
    immediateFetch(search, validCat, 1, pageSize);
    syncUrl(search, validCat, 1, pageSize);
  }, [search, pageSize, immediateFetch, syncUrl]);

  const setPage = useCallback((newPage: number) => {
    const validPage = Math.max(1, Math.min(newPage, 1000));
    setPageState(validPage);
    immediateFetch(search, category, validPage, pageSize);
    syncUrl(search, category, validPage, pageSize);
    if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [search, category, pageSize, immediateFetch, syncUrl]);

  const setPageSize = useCallback((newSize: number) => {
    const validSize = Math.max(1, Math.min(newSize, 50));
    setPageSizeState(validSize);
    setPageState(1);
    immediateFetch(search, category, 1, validSize);
    syncUrl(search, category, 1, validSize);
  }, [search, category, immediateFetch, syncUrl]);

  const clearSearch = useCallback(() => {
    setSearchState('');
    setPageState(1);
    immediateFetch('', category, 1, pageSize);
    syncUrl('', category, 1, pageSize);
  }, [category, pageSize, immediateFetch, syncUrl]);

  const clearFilters = useCallback(() => {
    setSearchState('');
    setCategoryState('all');
    setPageState(1);
    immediateFetch('', 'all', 1, pageSize);
    syncUrl('', 'all', 1, pageSize);
  }, [pageSize, immediateFetch, syncUrl]);

  const retry = useCallback(() => {
    immediateFetch(search, category, page, pageSize);
  }, [search, category, page, pageSize, immediateFetch]);

  return {
    items,
    categories,
    total,
    page,
    pageSize,
    totalPages,
    hasMore,
    loading,
    error,
    search,
    category,
    setSearch,
    setCategory,
    setPage,
    setPageSize,
    clearSearch,
    clearFilters,
    retry,
    hasActiveFilters,
    resultCountLabel,
  };
}

export const USE_USE_CASES_VERSION = '1.0.0';
