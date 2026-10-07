import React from 'react';
export interface SectionHeaderProps { badge?:string; title:string; description?:string; align?:'left'|'center'; className?:string; }
export function SectionHeader({badge,title,description,align='left',className=''}:SectionHeaderProps){
  return (<div className={`${align==='center'?'text-center':'text-left'} ${className}`}>{badge&&(<div className="mb-4 inline-flex items-center rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300 backdrop-blur">{badge}</div>)}<h2 className="bg-gradient-to-b from-white to-white/70 bg-clip-text text-3xl font-bold tracking-tight text-transparent sm:text-4xl">{title}</h2>{description&&(<p className="mt-4 max-w-2xl text-base leading-relaxed text-white/60 sm:text-lg">{description}</p>)}</div>);
}
