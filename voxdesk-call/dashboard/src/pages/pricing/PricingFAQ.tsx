import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const FAQS = [
  {
    question: 'How are included voice minutes displayed?',
    answer: 'The plan table reads included voice minutes from the active billing catalogue. Call usage and invoice status are shown in the authenticated billing area after records exist.',
  },
  {
    question: 'Does this page estimate my complete bill?',
    answer: 'No. It shows configured base plan prices and allowances only. Provider, model, voice, taxes, and overage details can change the total; the calculator does not invent those rates.',
  },
  {
    question: 'Can I bring my own telephony provider?',
    answer: 'Provider options depend on deployment and tenant configuration. Check the authenticated integration and telephony status before attempting a live call.',
  },
  {
    question: 'How do I confirm current billing and subscription state?',
    answer: 'Sign in and open Billing to read persisted plan, subscription, usage, and invoice state. Mutations remain subject to the server-side billing permissions and provider configuration.',
  },
];

export function PricingFAQ() {
  return (
    <section aria-labelledby="pricing-faq-heading" className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <h2 id="pricing-faq-heading" className="text-2xl font-bold text-white sm:text-3xl">
        Pricing and billing FAQ
      </h2>
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        {FAQS.map((item) => (
          <GlassCard key={item.question} className="p-6">
            <h3 className="text-sm font-semibold text-white">{item.question}</h3>
            <p className="mt-2 text-xs leading-relaxed text-white/65">{item.answer}</p>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}

export default PricingFAQ;
