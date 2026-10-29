/**
 * dashboard/src/app/providers.tsx
 *
 * The app-wide React Query client.
 *
 * Retries are disabled for mutations: every mutation here is a carrier side
 * effect, and the server already stores a durable receipt keyed on the
 * client's `Idempotency-Key`. Retrying in the client on top of that would
 * turn a slow response into a duplicate action, which is exactly what the
 * receipt exists to prevent.
 */
import React, { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
      mutations: { retry: 0 },
    },
  });
}

export interface ProvidersProps {
  children: React.ReactNode;
  client?: QueryClient;
}

export function Providers({ children, client }: ProvidersProps) {
  // One client per mount. Creating it in module scope would leak state between
  // tests and between server-side renders.
  const [queryClient] = useState<QueryClient>(() => client ?? createQueryClient());
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

export default Providers;
