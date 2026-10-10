/**
 * dashboard/src/api/use-cases.ts
 * Real backend integration via client.ts, no hardcoded URLs, no fetch, AbortController support.
 * Public endpoints: GET /api/v1/public/use-cases and /api/v1/public/use-cases/{slug}
 */
import { client } from './client';
import type { UseCaseListResponse, UseCaseDetailResponse, UseCaseCategoriesResponse, UseCaseSearchParams } from '../types/use-case';

const PUBLIC_BASE = '/api/v1/public/use-cases';
const MAX_SEARCH_LENGTH = 200;
const MAX_SLUG_LENGTH = 200;
const SLUG_REGEX = /^[a-z0-9-]+$/;
const CATEGORY_REGEX = /^[a-z0-9-]+$/;

function validateSearchParams(params: UseCaseSearchParams): void {
  if (params.q && params.q.length > MAX_SEARCH_LENGTH) {
    throw new Error(`Search query too long (max ${MAX_SEARCH_LENGTH})`);
  }
  if (params.category && params.category.length > 100) {
    throw new Error('Category too long');
  }
  if (params.category && params.category !== 'all' && !CATEGORY_REGEX.test(params.category)) {
    throw new Error('Invalid category format');
  }
  if (params.page && (params.page < 1 || params.page > 1000)) {
    throw new Error('Invalid page');
  }
  if (params.page_size && (params.page_size < 1 || params.page_size > 50)) {
    throw new Error('Invalid page_size');
  }
}

function validateSlug(slug: string): void {
  if (!slug || typeof slug !== 'string') throw new Error('Slug required');
  if (slug.length > MAX_SLUG_LENGTH) throw new Error(`Slug too long (max ${MAX_SLUG_LENGTH})`);
  if (!SLUG_REGEX.test(slug)) throw new Error('Invalid slug format — only lowercase alphanumeric and hyphen');
}

function buildQuery(params: UseCaseSearchParams): string {
  const sp = new URLSearchParams();
  if (params.q) sp.set('q', params.q.slice(0, MAX_SEARCH_LENGTH));
  if (params.category && params.category !== 'all') sp.set('category', params.category);
  if (params.page) sp.set('page', String(params.page));
  if (params.page_size) sp.set('page_size', String(params.page_size));
  if (params.sort) sp.set('sort', params.sort);
  return sp.toString();
}

export async function getUseCases(params: UseCaseSearchParams = {}, signal?: AbortSignal): Promise<UseCaseListResponse> {
  validateSearchParams(params);
  const qs = buildQuery(params);
  const url = qs ? `${PUBLIC_BASE}?${qs}` : PUBLIC_BASE;
  const res = await client.get<UseCaseListResponse>(url, { signal, authenticated: false, credentials: 'omit' });
  if (!res || !res.data) throw new Error('Invalid response from server');
  return res;
}

export async function getUseCaseBySlug(slug: string, signal?: AbortSignal): Promise<UseCaseDetailResponse> {
  validateSlug(slug);
  const url = `${PUBLIC_BASE}/${encodeURIComponent(slug)}`;
  const res = await client.get<UseCaseDetailResponse>(url, { signal, authenticated: false, credentials: 'omit' });
  if (!res || !res.data) throw new Error('Use case not found');
  return res;
}

export async function getUseCaseCategories(signal?: AbortSignal): Promise<UseCaseCategoriesResponse> {
  const url = `${PUBLIC_BASE}/categories`;
  const res = await client.get<UseCaseCategoriesResponse>(url, { signal, authenticated: false, credentials: 'omit' });
  if (!res || !res.data) throw new Error('Invalid categories response');
  return res;
}

export async function searchUseCases(query: string, category?: string, signal?: AbortSignal): Promise<UseCaseListResponse> {
  if (query && query.length > MAX_SEARCH_LENGTH) throw new Error(`Query too long (max ${MAX_SEARCH_LENGTH})`);
  return getUseCases({ q: query, category, page: 1, page_size: 12 }, signal);
}

export async function getFeaturedUseCases(signal?: AbortSignal): Promise<UseCaseListResponse> {
  return getUseCases({ page: 1, page_size: 6 }, signal);
}

export async function getUseCasesByCategory(category: string, page = 1, signal?: AbortSignal): Promise<UseCaseListResponse> {
  if (category !== 'all' && !CATEGORY_REGEX.test(category)) throw new Error('Invalid category');
  return getUseCases({ category, page, page_size: 12 }, signal);
}

// Additional production helpers for caching, retry, etc.
export interface UseCaseCacheEntry<T> {
  data: T;
  timestamp: number;
  expiresAt: number;
}

const cache = new Map<string, UseCaseCacheEntry<any>>();
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

function getCacheKey(url: string): string {
  return `use-case:${url}`;
}

function getFromCache<T>(key: string): T | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() > entry.expiresAt) {
    cache.delete(key);
    return null;
  }
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now(), expiresAt: Date.now() + CACHE_TTL_MS });
}

export function clearUseCaseCache(): void {
  cache.clear();
}

export function getCacheStats(): { size: number; keys: string[] } {
  return { size: cache.size, keys: Array.from(cache.keys()) };
}

export async function getUseCasesCached(params: UseCaseSearchParams = {}, signal?: AbortSignal): Promise<UseCaseListResponse> {
  const qs = buildQuery(params);
  const url = qs ? `${PUBLIC_BASE}?${qs}` : PUBLIC_BASE;
  const key = getCacheKey(url);
  const cached = getFromCache<UseCaseListResponse>(key);
  if (cached) return cached;
  const res = await getUseCases(params, signal);
  setCache(key, res);
  return res;
}

export async function getUseCaseBySlugCached(slug: string, signal?: AbortSignal): Promise<UseCaseDetailResponse> {
  validateSlug(slug);
  const key = getCacheKey(`${PUBLIC_BASE}/${slug}`);
  const cached = getFromCache<UseCaseDetailResponse>(key);
  if (cached) return cached;
  const res = await getUseCaseBySlug(slug, signal);
  setCache(key, res);
  return res;
}

// Error handling helpers
export function isNotFoundError(error: any): boolean {
  return error?.status === 404 || error?.message?.toLowerCase().includes('not found');
}

export function isValidationError(error: any): boolean {
  return error?.status === 422 || error?.message?.toLowerCase().includes('validation');
}

export function isNetworkError(error: any): boolean {
  return error?.name === 'AbortError' || error?.message?.toLowerCase().includes('network') || !navigator.onLine;
}

export function getErrorMessage(error: any): string {
  if (isNotFoundError(error)) return 'Use case not found';
  if (isValidationError(error)) return 'Invalid request — please check your search or category';
  if (isNetworkError(error)) return 'Network error — please check your connection';
  return error?.message || 'Something went wrong — please try again';
}

export function shouldRetry(error: any): boolean {
  return isNetworkError(error) || error?.status >= 500;
}

export async function retryWithBackoff<T>(fn: () => Promise<T>, maxRetries = 3, baseDelay = 1000): Promise<T> {
  let lastError: any;
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn();
    } catch (e) {
      lastError = e;
      if (!shouldRetry(e) || i === maxRetries - 1) throw e;
      const delay = baseDelay * Math.pow(2, i);
      await new Promise(r => setTimeout(r, delay));
    }
  }
  throw lastError;
}

// Additional exhaustive production helpers
export function formatUseCaseSlug(slug: string): string {
  return slug.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/--+/g, '-').replace(/^-|-$/g, '').slice(0, MAX_SLUG_LENGTH);
}

export function buildUseCaseUrl(slug: string): string {
  return `/use-cases/${formatUseCaseSlug(slug)}`;
}

export function buildCategoryUrl(category: string): string {
  if (!category || category === 'all') return '/use-cases';
  return `/use-cases?category=${encodeURIComponent(category)}`;
}

export function buildSearchUrl(query: string, category?: string): string {
  const params = new URLSearchParams();
  if (query) params.set('q', query.slice(0, MAX_SEARCH_LENGTH));
  if (category && category !== 'all') params.set('category', category);
  const qs = params.toString();
  return qs ? `/use-cases?${qs}` : '/use-cases';
}

export const USE_CASES_API_VERSION = '1.0.0';
export const USE_CASES_API_BASE = PUBLIC_BASE;
