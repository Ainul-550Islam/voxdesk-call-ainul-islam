import React, { useState } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function ContactForm() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [volume, setVolume] = useState('10k-50k');
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !email) return;
    setSubmitted(true);
  };

  return (
    <GlassCard className="p-6 sm:p-8">
      <h2 className="text-lg font-bold text-white">Request Architecture Review</h2>
      {submitted ? (
        <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-4 text-xs text-emerald-300">
          Thank you, {name}. Our solutions engineering team has logged your inquiry ({volume} monthly minutes).
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <label htmlFor="contact-name" className="block text-xs font-medium text-white/75">Full Name</label>
            <input id="contact-name" value={name} onChange={(e) => setName(e.target.value)} className="input-glass mt-1" placeholder="Alex Rivera" />
          </div>
          <div>
            <label htmlFor="contact-email" className="block text-xs font-medium text-white/75">Work Email</label>
            <input id="contact-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="input-glass mt-1" placeholder="alex@enterprise.com" />
          </div>
          <div>
            <label htmlFor="contact-vol" className="block text-xs font-medium text-white/75">Estimated Monthly Call Minutes</label>
            <select id="contact-vol" value={volume} onChange={(e) => setVolume(e.target.value)} className="input-glass mt-1 bg-black">
              <option value="<10k">Under 10,000 min/mo</option>
              <option value="10k-50k">10,000 – 50,000 min/mo</option>
              <option value="50k-250k">50,000 – 250,000 min/mo</option>
              <option value="250k+">250,000+ min/mo</option>
            </select>
          </div>
          <button type="submit" className="w-full rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-black hover:bg-white/90">
            Submit Inquiry →
          </button>
        </form>
      )}
    </GlassCard>
  );
}
export default ContactForm;
