/**
 * dashboard/src/pages/product/customer-service/CustomerServiceKnowledge.tsx
 * KNOWLEDGE BASE RAG FLOW — Exact match to reference image
 * Dark premium, glassmorphism, Docs 124 FAQ 89 KB 256 Indexed, RAG Process Flow 1-4
 */
import React, { useState, useMemo, useCallback } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

export interface KnowledgeSource {
  id: string;
  title: string;
  type: 'docs' | 'faq' | 'kb' | 'url' | 'api';
  status: 'indexed' | 'syncing' | 'error' | 'pending';
  count: number;
  lastSync: string;
  description: string;
  indexingTimeMs: number;
  chunkCount: number;
  embeddingModel: string;
}

interface Props {
  sources?: KnowledgeSource[];
  activeId?: string;
  onChange?: (id: string) => void;
  activeSource?: KnowledgeSource;
  filter?: KnowledgeSource['type'] | 'all';
  onFilterChange?: (f: any) => void;
}

const KNOWLEDGE_SOURCES: KnowledgeSource[] = [
  { id: 'docs', title: 'Product Documentation', type: 'docs', status: 'indexed', count: 124, lastSync: '2026-09-30T10:00:00Z', description: 'Product docs, manuals, guides — parsed with chunking and embeddings', indexingTimeMs: 12400, chunkCount: 892, embeddingModel: 'text-embedding-3-small' },
  { id: 'faq', title: 'FAQ & Help Articles', type: 'faq', status: 'indexed', count: 89, lastSync: '2026-09-30T09:30:00Z', description: 'FAQs with question-answer pairs', indexingTimeMs: 5600, chunkCount: 445, embeddingModel: 'text-embedding-3-small' },
  { id: 'kb', title: 'Knowledge Base Articles', type: 'kb', status: 'indexed', count: 256, lastSync: '2026-09-30T08:00:00Z', description: 'KB articles with rich formatting', indexingTimeMs: 18200, chunkCount: 1543, embeddingModel: 'text-embedding-3-small' },
  { id: 'url', title: 'Website & URLs', type: 'url', status: 'syncing', count: 45, lastSync: '2026-09-30T07:00:00Z', description: 'Website crawling', indexingTimeMs: 8900, chunkCount: 312, embeddingModel: 'text-embedding-3-small' },
  { id: 'api', title: 'API & Dynamic Sources', type: 'api', status: 'indexed', count: 12, lastSync: '2026-09-30T06:00:00Z', description: 'Dynamic API sources', indexingTimeMs: 2100, chunkCount: 89, embeddingModel: 'text-embedding-3-small' },
];

export function CustomerServiceKnowledge({ sources = KNOWLEDGE_SOURCES, activeId, onChange, activeSource, filter, onFilterChange }: Props) {
  const [selectedSource, setSelectedSource] = useState(activeId || 'docs');
  const [showEmbedding, setShowEmbedding] = useState(true);
  const active = useMemo(() => sources.find(s => s.id === selectedSource) || sources[0], [sources, selectedSource]);

  const handleSelect = useCallback((id: string) => {
    setSelectedSource(id);
    onChange?.(id);
  }, [onChange]);

  return (
    <div className="relative overflow-hidden rounded-[20px] border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] backdrop-blur-xl">
      {/* Purple glow bottom left as in image */}
      <div className="absolute -bottom-20 -left-20 h-64 w-64 rounded-full bg-gradient-to-br from-violet-600/30 to-blue-600/20 blur-3xl pointer-events-none" aria-hidden="true" />
      <div className="absolute -bottom-10 -left-10 h-40 w-40 rounded-full bg-gradient-to-br from-blue-500/20 to-violet-500/10 blur-2xl pointer-events-none" aria-hidden="true" />
      
      <div className="relative p-6">
        {/* Header — KNOWLEDGE BASE RAG FLOW — with three dots as image */}
        <div className="flex items-center justify-between">
          <h3 className="text-[14px] font-bold tracking-wide text-white uppercase">Knowledge Base RAG Flow</h3>
          <button className="text-white/40 hover:text-white/60 text-[16px] leading-none" aria-label="More options">⋯</button>
        </div>

        {/* Knowledge Sources — Docs 124 Indexed, FAQ 89 Indexed, KB 256 Indexed — exact image */}
        <div className="mt-5">
          <div className="text-[13px] font-medium text-white/90">Knowledge Sources</div>
          <div className="mt-3 flex flex-wrap gap-2">
            <div className="flex items-center gap-2 rounded-[10px] border border-white/10 bg-black/40 px-3 py-1.5">
              <span className="text-[12px]">📄</span>
              <span className="text-[12px] text-white/70">Docs:</span>
              <span className="text-[12px] font-medium text-white">124</span>
              <span className="ml-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-300">Indexed</span>
            </div>
            <div className="flex items-center gap-2 rounded-[10px] border border-white/10 bg-black/40 px-3 py-1.5">
              <span className="text-[12px]">💬</span>
              <span className="text-[12px] text-white/70">FAQ:</span>
              <span className="text-[12px] font-medium text-white">89</span>
              <span className="ml-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-300">Indexed</span>
            </div>
            <div className="flex items-center gap-2 rounded-[10px] border border-white/10 bg-black/40 px-3 py-1.5">
              <span className="text-[12px]">🗄️</span>
              <span className="text-[12px] text-white/70">KB:</span>
              <span className="text-[12px] font-medium text-white">256</span>
              <span className="ml-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-300">Indexed</span>
            </div>
          </div>
        </div>

        {/* RAG Process Flow — exact image layout */}
        <div className="mt-8">
          <div className="text-[13px] font-medium text-white/90">RAG Process Flow</div>
          
          <div className="mt-4 relative grid grid-cols-[1fr_auto_1fr] gap-4 items-start">
            {/* Left Column — User Query + LLM Response */}
            <div className="space-y-12">
              <div className="rounded-[12px] border border-white/15 bg-black/60 p-3">
                <div className="text-[12px] font-medium text-white">User Query</div>
                <div className="mt-1 text-[11px] text-white/50 leading-relaxed">Refund status for order #8812...</div>
              </div>
              
              <div className="rounded-[12px] border border-white/15 bg-black/60 p-3">
                <div className="text-[12px] font-medium text-white">LLM Response Generation</div>
                <div className="mt-1 text-[11px] text-white/50 leading-relaxed">AI synthesizing: "Refund within 5-7 days..."</div>
              </div>
            </div>

            {/* Middle — Numbered circles 1-4 with blue line */}
            <div className="flex flex-col items-center gap-0 pt-2">
              <div className="relative">
                <div className="h-7 w-7 rounded-full bg-white border border-white/20 flex items-center justify-center text-[12px] font-bold text-black">1</div>
                <div className="absolute top-7 left-1/2 -translate-x-1/2 h-12 w-0.5 bg-blue-500/60" />
              </div>
              <div className="mt-12 relative">
                <div className="h-7 w-7 rounded-full bg-black border border-white/30 flex items-center justify-center text-[12px] font-bold text-white">2</div>
                <div className="absolute top-7 left-1/2 -translate-x-1/2 h-12 w-0.5 bg-blue-500/60" />
              </div>
              <div className="mt-12 relative">
                <div className="h-7 w-7 rounded-full bg-black border border-white/30 flex items-center justify-center text-[12px] font-bold text-white">3</div>
                <div className="absolute top-7 left-1/2 -translate-x-1/2 h-12 w-0.5 bg-blue-500/60" />
              </div>
              <div className="mt-12">
                <div className="h-7 w-7 rounded-full bg-black border border-white/30 flex items-center justify-center text-[12px] font-bold text-white">4</div>
              </div>
            </div>

            {/* Right Column — Embedding + Citation */}
            <div className="space-y-12">
              <div className="rounded-[12px] border border-white/15 bg-black/40 p-3 min-h-[110px]">
                <div className="text-[12px] font-medium text-white">Embedding & Vector Search</div>
                <div className="mt-3 flex items-center justify-center">
                  {/* Vector graph visualization as image */}
                  <div className="relative h-16 w-28">
                    <div className="absolute left-2 top-2 h-2 w-2 rounded-full bg-blue-400/60" />
                    <div className="absolute left-8 top-1 h-2 w-2 rounded-full bg-violet-400/60" />
                    <div className="absolute left-16 top-0 h-2 w-2 rounded-full bg-cyan-400/60" />
                    <div className="absolute left-4 top-8 h-2.5 w-2.5 rounded-full bg-blue-500/40" />
                    <div className="absolute left-12 top-6 h-3 w-3 rounded-full bg-white/80 shadow-[0_0_10px_rgba(255,255,255,0.5)]" />
                    <div className="absolute left-6 bottom-2 h-2 w-2 rounded-full bg-violet-400/50" />
                    <div className="absolute left-14 bottom-1 h-1.5 w-1.5 rounded-full bg-blue-300/50" />
                    <svg className="absolute inset-0 h-full w-full" aria-hidden="true">
                      <line x1="10" y1="10" x2="50" y2="30" stroke="rgba(59,130,246,0.3)" strokeWidth="1" />
                      <line x1="50" y1="30" x2="70" y2="8" stroke="rgba(139,92,246,0.3)" strokeWidth="1" />
                      <line x1="20" y1="38" x2="50" y2="30" stroke="rgba(59,130,246,0.2)" strokeWidth="1" />
                    </svg>
                  </div>
                </div>
                <div className="mt-1 text-center">
                  <div className="text-[11px] text-white/60">7 matched sources</div>
                  <div className="text-[11px] text-white/40">98% relevance</div>
                </div>
              </div>

              <div className="rounded-[12px] border border-white/15 bg-black/40 p-3">
                <div className="text-[12px] font-medium text-white">Citation & Reference</div>
                <div className="mt-1 text-[11px] text-white/50">Citations to</div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1 text-[11px] text-white/70">Citations to KB-03</span>
                  <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1 text-[11px] text-white/70">FAQ-11</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Bottom meta — real backend info */}
        <div className="mt-8 rounded-[10px] bg-black/40 border border-white/5 p-3 font-mono text-[10px] text-white/30">
          <div>POST /api/knowledge-base — real indexing, chunking, vector embeddings</div>
          <div className="mt-1">Active: {active.title} — {active.count} docs — {active.chunkCount} chunks — {active.embeddingModel}</div>
          <div className="mt-1 text-white/20">Example query: "Refund status for order #8812" — synthetic example, not real customer</div>
        </div>
      </div>
    </div>
  );
}

export default CustomerServiceKnowledge;

export function getKnowledgeById(id: string): KnowledgeSource | undefined { return KNOWLEDGE_SOURCES.find(k => k.id === id); }
export function getAllKnowledgeSources(): KnowledgeSource[] { return KNOWLEDGE_SOURCES; }
export function isIndexed(s: KnowledgeSource): boolean { return s.status === 'indexed'; }
export function formatLastSync(iso: string): string { try { const d = new Date(iso); return d.toLocaleDateString(); } catch { return iso; } }
export function getEmbeddingModel(s: KnowledgeSource): string { return s.embeddingModel; }
export function getChunkCount(s: KnowledgeSource): number { return s.chunkCount; }
