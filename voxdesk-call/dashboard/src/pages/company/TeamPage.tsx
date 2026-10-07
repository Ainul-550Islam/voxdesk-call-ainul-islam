import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function TeamPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Company information</p>
        <h1 className="mt-4 text-4xl font-bold tracking-tight text-white sm:text-5xl">Team information</h1>
        <p className="mt-5 text-base leading-7 text-white/65">
          No named leadership roster, employee biographies, or verified organizational chart is published on this site. This page does not invent team members or attribute individual experience.
        </p>
        <a href="/about" className="mt-7 inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/5">About the product</a>
      </main>
      <PublicFooter />
    </div>
  );
}

export default TeamPage;
