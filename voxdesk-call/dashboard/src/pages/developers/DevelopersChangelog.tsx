import React from 'react';

export function DevelopersChangelog() {
  return (
    <section aria-labelledby="developer-changelog-title" className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
        <h2 id="developer-changelog-title" className="text-xl font-semibold text-white">Public release changelog</h2>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/60">
          A dated, customer-facing VoxDesk platform release changelog is not published by this page. Repository commits, migration counts, route registrations, and test runs are not substitutes for release notes and are not presented as customer launches.
        </p>
      </div>
    </section>
  );
}

export default DevelopersChangelog;
