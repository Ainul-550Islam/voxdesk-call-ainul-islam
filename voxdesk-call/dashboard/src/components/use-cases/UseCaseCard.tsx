import React from 'react';
import { UseCaseCategoryBadge } from './UseCaseCategoryBadge';
import type { UseCaseSummary } from '../../types/use-case';

interface Props {
  useCase: UseCaseSummary;
  featured?: boolean;
  position?: number;
  onClick?: (slug: string) => void;
  className?: string;
}

export function UseCaseCard({ useCase, featured = false, position = 0, onClick, className = '' }: Props) {
  const handleClick = () => {
    if (onClick) onClick(useCase.slug);
    else window.location.href = `/use-cases/${useCase.slug}`;
  };
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  };
  return (
    <article
      role="button"
      tabIndex={0}
      aria-label={`${useCase.title}, ${useCase.category} category`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className={`group relative overflow-hidden rounded-[20px] border bg-white/[0.03] p-5 sm:p-6 transition-all duration-300 hover:bg-white/[0.06] hover:border-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30 ${featured ? 'border-white/20 bg-white/[0.06] shadow-xl' : 'border-white/10'} ${className}`}
    >
      <div className="absolute inset-0 bg-gradient-to-br from-white/[0.03] to-transparent opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
      <div className="relative">
        <div className="flex items-start justify-between gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 text-lg" aria-hidden="true">
            {useCase.icon || '✨'}
          </div>
          {featured && <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-medium text-black">Featured</span>}
        </div>
        <div className="mt-4">
          <UseCaseCategoryBadge category={useCase.category} title={useCase.category_title} size="sm" />
        </div>
        <h3 className="mt-3 text-[15px] font-semibold text-white group-hover:text-white/90 line-clamp-2">{useCase.title}</h3>
        <p className="mt-2 text-sm leading-relaxed text-white/60 line-clamp-3">{useCase.description}</p>
        {useCase.capabilities && useCase.capabilities.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {useCase.capabilities.slice(0, 3).map((cap) => (
              <span key={cap} className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-white/50 border border-white/5">{cap}</span>
            ))}
            {useCase.capabilities.length > 3 && <span className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-white/40">+{useCase.capabilities.length - 3}</span>}
          </div>
        )}
        <div className="mt-4 flex items-center gap-1.5 text-xs font-medium text-white/40 group-hover:text-white/60">
          <span>Explore</span>
          <span className="transition-transform group-hover:translate-x-0.5" aria-hidden="true">→</span>
        </div>
      </div>
    </article>
  );
}

export default UseCaseCard;
