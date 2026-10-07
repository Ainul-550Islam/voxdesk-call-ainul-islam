import React from 'react';

export function ContactCTA() {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-xs text-white/65">
      Prefer to test immediately? <a href="/signup" className="text-blue-400 font-semibold hover:text-blue-300">Create a self-serve workspace →</a>
    </div>
  );
}
export default ContactCTA;
