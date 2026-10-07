import React from 'react';

const ROUTES = [
  {
    method: 'GET',
    path: '/api/v1/public/use-cases',
    access: 'Public',
    purpose: 'Paginated public catalog examples. Entries do not assert tenant configuration or production outcomes.',
  },
  {
    method: 'GET',
    path: '/api/v1/public/site/pricing',
    access: 'Public',
    purpose: 'Server-returned public plan catalogue; confirm any seeded catalogue disclosure shown by the pricing page.',
  },
  {
    method: 'GET',
    path: '/api/analytics/overview',
    access: 'Authenticated · analytics:read',
    purpose: 'Tenant-scoped analytics aggregate for the requested reporting range.',
  },
  {
    method: 'GET',
    path: '/api/v1/parity/capabilities',
    access: 'Authenticated · analytics:read',
    purpose: 'Backend-registered route evidence; registration is not operation-level verification.',
  },
  {
    method: 'GET',
    path: '/api/v1/parity/integrations',
    access: 'Authenticated · integration:read',
    purpose: 'Tenant-scoped integration status without returning secret credential values.',
  },
  {
    method: 'GET',
    path: '/api/v1/parity/e2e/inspect',
    access: 'Authenticated · call:read',
    purpose: 'Read-only inspection of an existing tenant/environment agent and call lifecycle.',
  },
  {
    method: 'GET',
    path: '/api/integrations/crm/providers',
    access: 'Authenticated · integration:read',
    purpose: 'CRM provider identifiers, declared capabilities, and required field names.',
  },
  {
    method: 'GET',
    path: '/api/calendar/providers',
    access: 'Authenticated · integration:read',
    purpose: 'Calendar provider catalogue and declared capabilities.',
  },
];

export function DevelopersAPI() {
  return (
    <section aria-labelledby="developer-api-title" className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
      <div className="max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Route inventory</p>
        <h2 id="developer-api-title" className="mt-3 text-2xl font-bold text-white">Representative API paths</h2>
        <p className="mt-3 text-sm leading-6 text-white/60">
          These paths are drawn from route declarations and exercised repository flows. They are not a complete API reference. Permission names are the server-side access boundary; a client-side link or route listing does not grant access.
        </p>
      </div>
      <div className="mt-6 overflow-x-auto rounded-2xl border border-white/10">
        <table className="w-full min-w-[760px] border-collapse text-left text-xs">
          <thead className="bg-white/[0.04] text-white/65">
            <tr>
              <th scope="col" className="p-3 font-semibold">Method</th>
              <th scope="col" className="p-3 font-semibold">Path</th>
              <th scope="col" className="p-3 font-semibold">Access</th>
              <th scope="col" className="p-3 font-semibold">Purpose / boundary</th>
            </tr>
          </thead>
          <tbody>
            {ROUTES.map((route) => (
              <tr key={`${route.method}:${route.path}`} className="border-t border-white/10 align-top">
                <td className="whitespace-nowrap p-3 font-mono text-blue-200">{route.method}</td>
                <td className="whitespace-nowrap p-3 font-mono text-white/80">{route.path}</td>
                <td className="p-3 text-white/65">{route.access}</td>
                <td className="p-3 leading-5 text-white/55">{route.purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 text-xs leading-5 text-white/40">
        The backend disables its generated OpenAPI and interactive documentation routes in this build. Verify the deployed route and permission policy before using a path in production.
      </p>
    </section>
  );
}

export default DevelopersAPI;
