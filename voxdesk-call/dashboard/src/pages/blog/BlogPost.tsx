import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';

export function BlogPost({ slug }: { slug?: string }) {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
        <a href="/blog" className="text-xs font-medium text-blue-300 underline underline-offset-4">Back to updates</a>
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6" role="status">
          <h1 className="text-2xl font-bold text-white">Article not found</h1>
          <p className="mt-3 text-sm leading-6 text-white/60">{slug ? `No published article exists for “${slug}”.` : 'No published article was selected.'} The site does not generate article content from a URL slug.</p>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}

export default BlogPost;
