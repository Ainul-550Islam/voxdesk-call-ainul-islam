import React from 'react';
import { UseCaseCategoryBadge } from '../../components/use-cases/UseCaseCategoryBadge';

interface Props {
  title: string;
  description: string;
  category: string;
  categoryTitle?: string;
  icon?: string;
  slug: string;
  className?: string;
}

export function UseCaseHero({ title, description, category, categoryTitle, icon, slug, className = '' }: Props) {
  const createHref = `/dashboard/agents/new?useCase=${encodeURIComponent(slug)}`;
  return (
    <div className={`relative overflow-hidden rounded-[32px] border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] p-8 sm:p-10 ${className}`}>
      <div className="absolute inset-0 bg-gradient-to-br from-blue-500/[0.08] via-violet-500/[0.05] to-transparent" aria-hidden="true" />
      <div className="relative">
        <UseCaseCategoryBadge category={category} title={categoryTitle} size="md" />
        <div className="mt-6 flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-white/10 text-xl" aria-hidden="true">{icon || '✨'}</div>
          <div className="min-w-0 flex-1">
            <h1 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">{title}</h1>
            <p className="mt-3 text-[15px] leading-relaxed text-white/60 max-w-2xl">{description}</p>
          </div>
        </div>
        <div className="mt-8 flex flex-wrap gap-3">
          <a href={createHref} className="rounded-xl bg-white px-5 py-2.5 text-sm font-medium text-black hover:bg-white/90">Build an agent for this use case</a>
          <a href="/docs/use-cases" className="rounded-xl border border-white/15 bg-white/5 px-5 py-2.5 text-sm font-medium text-white hover:bg-white/10">View docs</a>
        </div>
      </div>
    </div>
  );
}

export default UseCaseHero;
