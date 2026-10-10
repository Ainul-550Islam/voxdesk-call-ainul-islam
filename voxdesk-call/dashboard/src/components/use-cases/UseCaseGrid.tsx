import React from 'react';
import { UseCaseCard } from './UseCaseCard';
import { UseCaseSkeleton } from './UseCaseSkeleton';
import { UseCaseEmptyState } from './UseCaseEmptyState';
import type { UseCaseSummary } from '../../types/use-case';

interface Props {
  items: UseCaseSummary[];
  loading?: boolean;
  error?: string | null;
  search?: string;
  category?: string;
  onRetry?: () => void;
  onClearFilters?: () => void;
  className?: string;
}

export function UseCaseGrid({ items, loading, error, search, category, onRetry, onClearFilters, className = '' }: Props) {
  if (loading) {
    return (
      <div className={`grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 ${className}`} aria-busy="true" aria-live="polite">
        {Array.from({ length: 6 }).map((_, i) => <UseCaseSkeleton key={i} />)}
      </div>
    );
  }
  if (error) {
    return (
      <div className={`rounded-[20px] border border-red-500/20 bg-red-500/5 p-8 text-center ${className}`} role="alert">
        <div className="text-sm font-medium text-red-300">Failed to load use cases</div>
        <div className="mt-2 text-xs text-red-200/70">{error}</div>
        {onRetry && <button onClick={onRetry} className="mt-4 rounded-xl bg-white px-4 py-2 text-xs font-medium text-black hover:bg-white/90">Retry</button>}
      </div>
    );
  }
  if (!items || items.length === 0) {
    return <UseCaseEmptyState search={search} category={category} onClear={onClearFilters} className={className} />;
  }
  return (
    <div className={`grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 ${className}`} role="list" aria-label="Use cases">
      {items.map((item, idx) => (
        <div key={item.slug} role="listitem">
          <UseCaseCard useCase={item} featured={!!item.featured} position={idx} />
        </div>
      ))}
    </div>
  );
}

export default UseCaseGrid;
