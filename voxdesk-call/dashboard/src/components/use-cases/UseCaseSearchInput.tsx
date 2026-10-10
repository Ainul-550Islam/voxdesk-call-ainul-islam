import React, { useState, useRef, useEffect } from 'react';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onClear?: () => void;
  placeholder?: string;
  loading?: boolean;
  className?: string;
}

export function UseCaseSearchInput({ value, onChange, onClear, placeholder = 'Search use cases…', loading, className = '' }: Props) {
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === '/' && !focused && (e.target as HTMLElement)?.tagName !== 'INPUT') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [focused]);

  return (
    <div className={`relative ${className}`}>
      <div className={`relative flex items-center rounded-[16px] border bg-white/[0.05] transition-colors ${focused ? 'border-white/20 bg-white/[0.08]' : 'border-white/10 hover:border-white/15'}`}>
        <span className="pl-4 text-white/40" aria-hidden="true">⌕</span>
        <input
          ref={inputRef}
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={placeholder}
          aria-label="Search use cases"
          aria-describedby="search-hint"
          className="w-full bg-transparent px-3 py-3.5 text-sm text-white placeholder:text-white/40 focus:outline-none"
        />
        {loading && <span className="pr-2 text-white/40 animate-pulse" aria-label="Searching">◍</span>}
        {value && onClear && (
          <button onClick={onClear} aria-label="Clear search" className="mr-2 rounded-full bg-white/10 p-1.5 text-white/60 hover:bg-white/15 hover:text-white">
            <span aria-hidden="true">✕</span>
          </button>
        )}
        <div className="hidden sm:flex items-center gap-1 pr-3">
          <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-white/40">⌘K</kbd>
        </div>
      </div>
      <div id="search-hint" className="sr-only">Search by title, description, category, or capability. Press slash to focus.</div>
    </div>
  );
}

export default UseCaseSearchInput;
