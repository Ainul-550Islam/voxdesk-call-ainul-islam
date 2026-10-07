import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function BlogPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">VoxDesk updates</p>
        <h1 className="mt-4 text-4xl font-bold tracking-tight text-white sm:text-5xl">Published articles</h1>
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6" role="status">
          <h2 className="text-base font-semibold text-white">No verified articles are published here</h2>
          <p className="mt-3 text-sm leading-6 text-white/60">The entries previously shown on this page were static claims rather than a verifiable publication feed. They are not presented as published engineering articles.</p>
          <a href="/resources" className="mt-5 inline-flex text-sm font-semibold text-blue-300 underline underline-offset-4">Browse product resources</a>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default BlogPage;
