import React from 'react';
import { GlassCard } from '../../components/ui/GlassCard';

export function DocsAPI() {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-bold text-white">REST API & OpenAPI Specification</h2>
      <p className="mt-2 text-xs leading-relaxed text-white/65">Authenticate with Bearer JWTs or API keys across all /api/v1/* endpoints.</p>
    </GlassCard>
  );
}
export default DocsAPI;
