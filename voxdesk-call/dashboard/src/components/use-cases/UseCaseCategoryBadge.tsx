import React from 'react';

interface Props {
  category: string;
  title?: string;
  size?: 'sm' | 'md' | 'lg';
  variant?: 'default' | 'outline' | 'solid';
  className?: string;
}

const CATEGORY_STYLES: Record<string, { bg: string; text: string; border: string; dot: string }> = {
  receptionists: { bg: 'bg-blue-500/10', text: 'text-blue-300', border: 'border-blue-500/20', dot: 'bg-blue-400' },
  'call-centers': { bg: 'bg-violet-500/10', text: 'text-violet-300', border: 'border-violet-500/20', dot: 'bg-violet-400' },
  industry: { bg: 'bg-emerald-500/10', text: 'text-emerald-300', border: 'border-emerald-500/20', dot: 'bg-emerald-400' },
  assistants: { bg: 'bg-amber-500/10', text: 'text-amber-300', border: 'border-amber-500/20', dot: 'bg-amber-400' },
  sales: { bg: 'bg-pink-500/10', text: 'text-pink-300', border: 'border-pink-500/20', dot: 'bg-pink-400' },
  all: { bg: 'bg-white/5', text: 'text-white/70', border: 'border-white/10', dot: 'bg-white/40' },
};

function getCategoryMeta(category: string) {
  const titles: Record<string, string> = {
    receptionists: 'Receptionists & Answering',
    'call-centers': 'Call Centers & Dialers',
    industry: 'Industry Voice Agents',
    assistants: 'AI Assistants & Agents',
    sales: 'Sales & Operations',
    all: 'All Use Cases',
  };
  return { title: titles[category] || category, style: CATEGORY_STYLES[category] || CATEGORY_STYLES['all'] };
}

export function UseCaseCategoryBadge({ category, title, size = 'md', variant = 'default', className = '' }: Props) {
  const meta = getCategoryMeta(category);
  const displayTitle = title || meta.title;
  const sizeClasses = {
    sm: 'px-2 py-0.5 text-[10px]',
    md: 'px-2.5 py-1 text-xs',
    lg: 'px-3 py-1.5 text-sm',
  };
  const variantClasses = {
    default: `${meta.style.bg} ${meta.style.text} ${meta.style.border} border`,
    outline: `bg-transparent ${meta.style.text} ${meta.style.border} border`,
    solid: `${meta.style.bg} ${meta.style.text} border-transparent`,
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-medium ${sizeClasses[size]} ${variantClasses[variant]} ${className}`} aria-label={`Category: ${displayTitle}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.style.dot}`} aria-hidden="true" />
      {displayTitle}
    </span>
  );
}

export default UseCaseCategoryBadge;
