import React, { useState } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

const SLOTS = ['10:00 AM EST', '11:30 AM EST', '2:00 PM EST', '4:15 PM EST'];

export function BookDemoCalendar() {
  const [selectedSlot, setSelectedSlot] = useState(SLOTS[1]);
  const [confirmed, setConfirmed] = useState(false);

  return (
    <GlassCard className="p-6 sm:p-8">
      <div className="text-xs font-semibold uppercase tracking-wider text-violet-300">Live Technical Demo</div>
      <h2 className="mt-2 text-lg font-bold text-white">Select a 30-Minute Architecture Slot</h2>
      <div className="mt-4 grid grid-cols-2 gap-2.5">
        {SLOTS.map((slot) => (
          <button
            key={slot}
            type="button"
            onClick={() => { setSelectedSlot(slot); setConfirmed(false); }}
            className={`rounded-xl border px-3 py-2.5 text-xs font-medium transition-colors ${
              selectedSlot === slot ? 'border-white bg-white text-black' : 'border-white/10 bg-white/5 text-white/75 hover:bg-white/10'
            }`}
          >
            {slot}
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={() => setConfirmed(true)}
        className="mt-6 w-full rounded-xl bg-blue-500/20 border border-blue-500/40 px-4 py-2.5 text-xs font-semibold text-blue-300 hover:bg-blue-500/30"
      >
        Confirm Slot ({selectedSlot})
      </button>
      {confirmed && (
        <div className="mt-3 text-xs text-emerald-300">
          Slot reserved for {selectedSlot}. Calendar invitation ready.
        </div>
      )}
    </GlassCard>
  );
}
export default BookDemoCalendar;
