import React from 'react';

export function UseCaseSkeleton() {
  return (
    <div className="animate-pulse rounded-[20px] border border-white/10 bg-white/[0.03] p-5 sm:p-6" aria-busy="true" aria-label="Loading use case">
      <div className="flex items-start justify-between">
        <div className="h-10 w-10 rounded-xl bg-white/10" />
        <div className="h-5 w-16 rounded-full bg-white/10" />
      </div>
      <div className="mt-4 h-4 w-24 rounded-full bg-white/10" />
      <div className="mt-3 h-5 w-3/4 rounded bg-white/10" />
      <div className="mt-2 space-y-2">
        <div className="h-4 w-full rounded bg-white/5" />
        <div className="h-4 w-2/3 rounded bg-white/5" />
      </div>
      <div className="mt-4 flex gap-2">
        <div className="h-5 w-16 rounded-full bg-white/5" />
        <div className="h-5 w-20 rounded-full bg-white/5" />
      </div>
    </div>
  );
}

export function UseCaseGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => <UseCaseSkeleton key={i} />)}
    </div>
  );
}

export default UseCaseSkeleton;
