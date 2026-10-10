import React from 'react';
import type { UseCaseCategory } from '../../types/use-case';

interface Props {
  categories: UseCaseCategory[];
  activeCategory: string;
  total: number;
  onCategoryChange: (cat: string) => void;
  className?: string;
}

export function UseCaseFilterBar({ categories, activeCategory, total, onCategoryChange, className = '' }: Props) {
  const allCats = categories.length ? categories : [{ id: 'all', title: 'All', slug: 'all' } as UseCaseCategory];
  return (
    <div className={`flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between ${className}`}>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter by category">
        {allCats.map((cat) => {
          const isActive = (cat.id || cat.slug) === activeCategory || (activeCategory === 'all' && cat.id === 'all');
          return (
            <button
              key={cat.id || cat.slug}
              role="tab"
              aria-selected={isActive}
              aria-label={`Filter by ${cat.title}`}
              onClick={() => onCategoryChange(cat.id || cat.slug || 'all')}
              className={`rounded-full px-4 py-2 text-xs font-medium transition-colors border ${isActive ? 'bg-white text-black border-white' : 'bg-white/[0.05] text-white/70 border-white/10 hover:bg-white/10 hover:text-white'}`}
            >
              {cat.title}
              {cat.count !== undefined && <span className="ml-1.5 opacity-60">({cat.count})</span>}
            </button>
          );
        })}
      </div>
      <div className="text-xs text-white/40" aria-live="polite" aria-atomic="true">
        {total} {total === 1 ? 'use case' : 'use cases'}
      </div>
    </div>
  );
}

export default UseCaseFilterBar;
