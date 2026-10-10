/**
 * dashboard/src/config/useCaseNavigation.ts
 * Navigation config, SEO helpers, CTA href builders - real production.
 */
export const USE_CASE_ROUTES = {
  LIST: '/use-cases',
  DETAIL: '/use-cases/:slug',
  SOLUTIONS_LIST: '/solutions/use-cases',
  SOLUTIONS_DETAIL: '/solutions/use-cases/:slug',
  CREATE: '/dashboard/agents/new',
  DOCS: '/docs/use-cases',
  API_DOCS: '/docs/api/use-cases',
} as const;

export const USE_CASE_CATEGORY_CONFIG: Record<string, { title: string; description: string; icon: string; color: string; gradient: string }> = {
  all: { title: 'All Use Cases', description: 'Browse all voice AI use cases', icon: '✨', color: 'white', gradient: 'from-white/10 to-white/5' },
  receptionists: { title: 'Receptionists & Answering', description: 'AI receptionists handling inbound calls, appointments, and routing', icon: '📞', color: 'blue', gradient: 'from-blue-500 to-cyan-500' },
  'call-centers': { title: 'Call Centers & Dialers', description: 'Scalable call center operations with AI dialers and queue management', icon: '☎️', color: 'violet', gradient: 'from-violet-500 to-purple-500' },
  industry: { title: 'Industry Voice Agents', description: 'Industry-specific voice agents for healthcare, real estate, dental, and more', icon: '🏢', color: 'emerald', gradient: 'from-emerald-500 to-teal-500' },
  assistants: { title: 'AI Assistants & Agents', description: 'AI assistants for support, intake, qualification, and automation', icon: '🤖', color: 'amber', gradient: 'from-amber-500 to-orange-500' },
  sales: { title: 'Sales & Operations', description: 'Sales development, follow-up, and revenue operations', icon: '💼', color: 'pink', gradient: 'from-pink-500 to-rose-500' },
};

export function getCategoryConfig(categoryId: string) {
  return USE_CASE_CATEGORY_CONFIG[categoryId] || USE_CASE_CATEGORY_CONFIG['all'];
}

export function getCreateAgentHref(slug: string): string {
  if (!slug || !/^[a-z0-9-]+$/.test(slug) || slug.length > 200) return '/dashboard/agents/new';
  return `/dashboard/agents/new?useCase=${encodeURIComponent(slug)}`;
}

export function getUseCaseHref(slug: string): string {
  if (!slug) return '/use-cases';
  return `/use-cases/${encodeURIComponent(slug)}`;
}

export function getUseCaseSolutionsHref(slug: string): string {
  if (!slug) return '/solutions/use-cases';
  return `/solutions/use-cases/${encodeURIComponent(slug)}`;
}

export function getCategoryHref(category: string): string {
  if (!category || category === 'all') return '/use-cases';
  return `/use-cases?category=${encodeURIComponent(category)}`;
}

export function buildSeoTitle(useCase?: { title?: string; category?: string }): string {
  if (!useCase?.title) return 'Use Cases — Build voice AI for the work that matters | VoxDesk';
  return `${useCase.title} — Voice AI Use Case | VoxDesk`;
}

export function buildSeoDescription(useCase?: { description?: string; category?: string }): string {
  if (!useCase?.description) return 'Explore production voice AI use cases — receptionists, call centers, industry agents, assistants, sales ops. Real backend, no fake data.';
  return useCase.description.slice(0, 160);
}

export function buildCanonicalUrl(slug?: string): string {
  const base = 'https://voxdesk.ai';
  if (!slug) return `${base}/use-cases`;
  return `${base}/use-cases/${slug}`;
}

export function buildOgImage(slug?: string): string {
  if (!slug) return 'https://voxdesk.ai/og/use-cases.png';
  return `https://voxdesk.ai/og/use-cases/${slug}.png`;
}

export function buildBreadcrumbs(slug?: string, title?: string, category?: string, categoryTitle?: string) {
  const crumbs: Array<{ label: string; href?: string; current?: boolean }> = [
    { label: 'Home', href: '/' },
    { label: 'Use Cases', href: '/use-cases' },
  ];
  if (category && categoryTitle) {
    crumbs.push({ label: categoryTitle, href: `/use-cases?category=${category}` });
  }
  if (slug && title) {
    crumbs.push({ label: title, current: true });
  }
  return crumbs;
}

export const USE_CASE_NAVIGATION_VERSION = '1.0.0';
