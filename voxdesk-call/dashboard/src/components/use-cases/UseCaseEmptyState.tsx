import React from 'react';

interface Props {
  search?: string;
  category?: string;
  onClear?: () => void;
  className?: string;
}

export function UseCaseEmptyState({ search, category, onClear, className = '' }: Props) {
  const hasSearch = !!search;
  const hasCategory = !!category && category !== 'all';
  return (
    <div className={`rounded-[20px] border border-dashed border-white/10 bg-white/[0.02] p-12 text-center ${className}`} role="status" aria-live="polite">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white/5 text-xl" aria-hidden="true">🔍</div>
      <div className="mt-4 text-sm font-medium text-white">
        {hasSearch ? `No results for "${search}"` : hasCategory ? `No use cases in ${category}` : 'No use cases found'}
      </div>
      <div className="mt-2 text-xs text-white/50 max-w-sm mx-auto">
        {hasSearch ? 'Try a different search term or clear filters. Real records only — no fake data.' : hasCategory ? 'Try selecting a different category or clear filters.' : 'No use cases configured yet. Backend returns empty, not inventing data.'}
      </div>
      {(hasSearch || hasCategory) && onClear && (
        <button onClick={onClear} className="mt-6 rounded-xl bg-white px-4 py-2 text-xs font-medium text-black hover:bg-white/90">
          Clear filters
        </button>
      )}
    </div>
  );
}

export default UseCaseEmptyState;
