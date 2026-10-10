import React, { useState } from 'react';
import type { UseCaseFAQ as FAQType } from '../../types/use-case';

interface Props {
  faqs: FAQType[];
  className?: string;
}

export function UseCaseFAQ({ faqs, className = '' }: Props) {
  const [openIdx, setOpenIdx] = useState<number | null>(0);
  if (!faqs || faqs.length === 0) {
    return (
      <div className={`rounded-[16px] border border-dashed border-white/10 bg-white/[0.02] p-6 text-center ${className}`}>
        <div className="text-xs text-white/40">No FAQs configured</div>
      </div>
    );
  }
  return (
    <div className={`space-y-2 ${className}`} role="region" aria-label="Frequently asked questions">
      {faqs.map((faq, idx) => {
        const isOpen = openIdx === idx;
        return (
          <div key={idx} className="rounded-[14px] border border-white/10 bg-white/[0.03]">
            <button
              aria-expanded={isOpen}
              aria-controls={`faq-${idx}`}
              onClick={() => setOpenIdx(isOpen ? null : idx)}
              className="flex w-full items-center justify-between gap-4 p-4 text-left"
            >
              <span className="text-sm font-medium text-white">{faq.question}</span>
              <span className={`shrink-0 text-white/40 transition-transform ${isOpen ? 'rotate-180' : ''}`} aria-hidden="true">⌄</span>
            </button>
            {isOpen && (
              <div id={`faq-${idx}`} className="px-4 pb-4 text-sm leading-relaxed text-white/60">
                {faq.answer}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default UseCaseFAQ;
