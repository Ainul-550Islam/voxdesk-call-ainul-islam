import React from 'react';

interface Message {
  role: 'user' | 'agent' | 'system';
  content: string;
  label?: string;
}

interface Props {
  messages: Message[];
  className?: string;
}

export function UseCaseExampleCall({ messages, className = '' }: Props) {
  if (!messages || messages.length === 0) {
    return (
      <div className={`rounded-[20px] border border-dashed border-white/10 bg-white/[0.02] p-6 text-center ${className}`}>
        <div className="text-xs text-white/40">No example conversation</div>
        <div className="mt-1 text-[11px] text-white/30">Labeled as Example — never REAL/LIVE/CUSTOMER.</div>
      </div>
    );
  }
  return (
    <div className={`rounded-[20px] border border-white/10 bg-white/[0.03] p-6 ${className}`}>
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-white">Example conversation</div>
        <div className="rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/20 px-2.5 py-1 text-[10px]">Example — demo only</div>
      </div>
      <div className="mt-1 text-xs text-white/50">Labeled explicitly as Example — demo data, no production use</div>
      <div className="mt-6 space-y-4 max-h-[400px] overflow-y-auto pr-2">
        {messages.map((msg, idx) => (
          <div key={idx} className={`flex gap-3 ${msg.role === 'user' ? '' : 'justify-end'}`}>
            {msg.role === 'user' && <div className="h-8 w-8 shrink-0 rounded-full bg-blue-500/20 flex items-center justify-center text-xs">U</div>}
            <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${msg.role === 'user' ? 'rounded-bl-sm bg-white/10 text-white/90' : 'rounded-br-sm bg-white text-black'}`}>
              {msg.label && <div className="text-[10px] opacity-60 mb-1">{msg.label}</div>}
              {msg.content}
            </div>
            {msg.role === 'agent' && <div className="h-8 w-8 shrink-0 rounded-full bg-white flex items-center justify-center text-xs text-black">AI</div>}
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-xl bg-amber-500/5 border border-amber-500/10 p-3 text-[11px] text-amber-200/70">
        This is an example conversation for demonstration — demo data only, no PII, no production metrics.
      </div>
    </div>
  );
}

export default UseCaseExampleCall;
