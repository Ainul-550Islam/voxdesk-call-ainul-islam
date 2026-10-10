import React from 'react';

export function UseCaseDetailSkeleton() {
  return (
    <div className="animate-pulse" aria-busy="true" aria-label="Loading use case detail">
      <div className="h-6 w-48 rounded bg-white/10" />
      <div className="mt-8 h-10 w-3/4 rounded bg-white/10" />
      <div className="mt-4 h-5 w-full rounded bg-white/5" />
      <div className="mt-2 h-5 w-2/3 rounded bg-white/5" />
      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-4">
          <div className="h-32 rounded-[20px] bg-white/5" />
          <div className="h-64 rounded-[20px] bg-white/5" />
        </div>
        <div className="space-y-4">
          <div className="h-48 rounded-[20px] bg-white/5" />
          <div className="h-32 rounded-[20px] bg-white/5" />
        </div>
      </div>
    </div>
  );
}

export default UseCaseDetailSkeleton;
