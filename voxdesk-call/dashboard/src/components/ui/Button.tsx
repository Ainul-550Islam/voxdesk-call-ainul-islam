import React from 'react';
export type ButtonVariant='primary'|'secondary'|'ghost'|'outline'; export type ButtonSize='sm'|'md'|'lg';
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> { variant?:ButtonVariant; size?:ButtonSize; loading?:boolean; leftIcon?:React.ReactNode; rightIcon?:React.ReactNode; }
export function Button({variant='primary',size='md',loading=false,leftIcon,rightIcon,children,className='',disabled,...props}:ButtonProps){
  const base='inline-flex items-center justify-center rounded-xl font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none';
  const variants:Record<ButtonVariant,string>={primary:'bg-gradient-to-r from-blue-600 to-violet-600 text-white hover:from-blue-700 hover:to-violet-700 shadow-lg shadow-blue-500/20 hover:shadow-xl hover:shadow-blue-500/30 focus-visible:ring-blue-500',secondary:'bg-white/10 backdrop-blur text-white hover:bg-white/20 border border-white/20 focus-visible:ring-white',ghost:'text-white/70 hover:text-white hover:bg-white/10 focus-visible:ring-white',outline:'border border-white/20 text-white hover:bg-white/10 focus-visible:ring-white'};
  const sizes:Record<ButtonSize,string>={sm:'h-8 px-3 text-sm',md:'h-10 px-5 text-sm',lg:'h-12 px-8 text-base'};
  return (<button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`} disabled={disabled||loading} {...props}>{loading&&<span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden="true" />}{leftIcon&&!loading&&<span className="mr-2" aria-hidden="true">{leftIcon}</span>}{children}{rightIcon&&<span className="ml-2" aria-hidden="true">{rightIcon}</span>}</button>);
}
