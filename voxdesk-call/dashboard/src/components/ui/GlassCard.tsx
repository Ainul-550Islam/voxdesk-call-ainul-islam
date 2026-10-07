import React from 'react';
export interface GlassCardProps { children:React.ReactNode; className?:string; hover?:boolean; glow?:boolean; padding?:'sm'|'md'|'lg'; }
export function GlassCard({children,className='',hover=false,glow=false,padding='md'}:GlassCardProps){
  const paddings={sm:'p-4',md:'p-6',lg:'p-8'};
  return (<div className={`relative rounded-2xl border border-white/10 bg-white/[0.05] backdrop-blur-xl ${paddings[padding]} ${hover?'transition-all hover:bg-white/[0.08] hover:border-white/20 hover:shadow-xl hover:shadow-black/20 hover:-translate-y-1':''} ${glow?'shadow-lg shadow-blue-500/10':''} ${className}`}><div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-b from-white/10 to-transparent opacity-50" aria-hidden="true" /><div className="relative">{children}</div></div>);
}
