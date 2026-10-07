import React from 'react';

export function BlogHero() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
      <div className="max-w-3xl">
        <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-3.5 py-1.5 text-xs font-medium text-blue-300">
          VoxDesk Engineering & Product Blog
        </div>
        <h1 className="mt-6 text-4xl font-bold tracking-tight text-white sm:text-5xl">
          Systems Engineering for Real-Time Voice AI
        </h1>
        <p className="mt-4 text-base leading-relaxed text-white/65">
          Deep dives into sub-second audio streaming, deterministic call transfer state machines, Rust concurrency leases, and enterprise telephony reliability.
        </p>
      </div>
    </section>
  );
}
export default BlogHero;
