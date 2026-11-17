// @ts-nocheck — vendored verbatim from dashboard/src (PART 17A); checked by vite-port/tsconfig.json
import React from 'react';
import { footerNavigation } from '../../config/navigation';

export function PublicFooter() {
  return (
    <footer className="border-t border-white/10 bg-black/50 backdrop-blur">
      <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
        <div className="grid grid-cols-2 gap-8 lg:grid-cols-5">
          <div className="col-span-2">
            <div className="flex items-center gap-2">
              <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-blue-600 to-violet-600" aria-hidden="true" />
              <span className="text-lg font-bold text-white">VoxDesk Voice AI</span>
            </div>
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-white/60">
              Voice AI tools for agent setup, campaigns, call operations, knowledge, and workflows. Provider availability depends on deployment configuration.
            </p>
            <div className="mt-6 flex gap-3">
              <a href="/status" className="text-xs text-white/50 hover:text-white/80" style={{ textDecoration: "underline", textUnderlineOffset: "0.15em" }}>Status</a>
              <span className="text-white/20">•</span>
              <span className="text-xs text-white/50">© 2026 VoxDesk</span>
            </div>
          </div>
          {Object.entries(footerNavigation).map(([key, items]) => (
            <div key={key}>
              <h3 className="text-sm font-semibold text-white capitalize">{key}</h3>
              <ul className="mt-4 space-y-3">
                {items.map((item) => (
                  <li key={item.id}>
                    <a href={item.href} className="text-sm text-white/60 hover:text-white transition-colors"
                      style={{ textDecoration: "underline", textUnderlineOffset: "0.15em" }}>
                      {item.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-12 border-t border-white/10 pt-8 flex flex-col sm:flex-row justify-between gap-4">
          <div className="flex gap-6 text-xs text-white/50">
            <a href="/privacy" style={{ textDecoration: "underline", textUnderlineOffset: "0.15em" }}>Privacy</a>
            <a href="/terms" style={{ textDecoration: "underline", textUnderlineOffset: "0.15em" }}>Terms</a>
            <a href="/security" style={{ textDecoration: "underline", textUnderlineOffset: "0.15em" }}>Security</a>
            <a href="/compliance" style={{ textDecoration: "underline", textUnderlineOffset: "0.15em" }}>Compliance</a>
          </div>
          <div className="text-xs text-white/30">Built for serious businesses. No fake metrics.</div>
        </div>
      </div>
    </footer>
  );
}

export default PublicFooter;
