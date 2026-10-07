import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const USE_CASES_CATALOG = [
  {
    slug: 'ai-receptionist',
    title: 'AI Receptionist',
    category: 'receptionists',
    category_title: 'Receptionists & Answering',
    description: 'Preview-only catalog entry for a voice-receptionist workflow concept. Phone, knowledge, calendar, and transfer behavior are not verified.',
    capabilities: ['knowledge-base', 'calendar-booking', 'warm-transfer'],
    supported: null,
    verified: false,
    featured: true,
    workflow: [
      { order: 1, id: 'answer-and-identify-caller', title: 'Answer & Identify Caller', description: 'Preview-only workflow step; caller memory and phone-number services are not verified by this fixture.' },
      { order: 2, id: 'understand-intent-and-retrieve-knowledge', title: 'Understand Intent & Retrieve Knowledge', description: 'Preview-only workflow step; retrieval behavior and tenant isolation are not verified by this fixture.' },
      { order: 3, id: 'book-slot-or-warm-transfer', title: 'Book Slot or Warm Transfer', description: 'Preview-only workflow step; calendar booking and provider transfer are not performed by this fixture.' },
    ],
  },
  {
    slug: 'customer-support',
    title: 'Customer Support Agent',
    category: 'assistants',
    category_title: 'Customer Support & Assistants',
    description: 'Preview-only catalog entry for a support workflow concept. Voice/chat, retrieval, ticket, and escalation behavior are not verified.',
    capabilities: ['knowledge-base', 'crm-tools', 'warm-transfer'],
    supported: null,
    verified: false,
    featured: true,
    workflow: [
      { order: 1, id: 'authenticate-and-recall-context', title: 'Authenticate & Recall Context', description: 'Preview-only workflow step; caller history and ticket integrations are not verified by this fixture.' },
      { order: 2, id: 'resolve-with-rag-and-tools', title: 'Resolve with RAG & Tools', description: 'Preview-only workflow step; retrieval and tool execution are not performed by this fixture.' },
      { order: 3, id: 'escalate-when-needed', title: 'Escalate When Needed', description: 'Preview-only workflow step; no transcript is transferred and no provider handoff is performed.' },
    ],
  },
  {
    slug: 'appointment-booking',
    title: 'Appointment Booking & Reminders',
    category: 'receptionists',
    category_title: 'Receptionists & Answering',
    description: 'Preview-only catalog entry for a scheduling workflow concept. Calendar availability, booking, and SMS behavior are not verified.',
    capabilities: ['calendar-booking', 'sms-followup', 'crm-tools'],
    supported: null,
    verified: false,
    featured: true,
    workflow: [
      { order: 1, id: 'qualify-service-type', title: 'Qualify Service Type', description: 'Preview-only workflow step; no caller details are collected by this fixture.' },
      { order: 2, id: 'check-calendar-slots', title: 'Check Calendar Slots', description: 'Preview-only workflow step; live calendar availability and timezone behavior are not verified.' },
      { order: 3, id: 'book-and-send-confirmation', title: 'Book & Send Confirmation', description: 'Preview-only workflow step; no calendar write or SMS is performed by this fixture.' },
    ],
  },
  {
    slug: 'outbound-lead-qualification',
    title: 'Outbound Lead Qualification',
    category: 'sales',
    category_title: 'Sales & Outbound',
    description: 'Preview-only catalog entry for a lead qualification workflow concept. Consent, DNC, carrier, and CRM write behavior are not verified.',
    capabilities: ['outbound-campaigns', 'dnc-compliance', 'crm-tools'],
    supported: null,
    verified: false,
    featured: false,
    workflow: [
      { order: 1, id: 'verify-dnc-and-calling-window', title: 'Verify DNC & Calling Window', description: 'Preview-only workflow step; no DNC registry or calling-window check is performed.' },
      { order: 2, id: 'qualify-lead-criteria', title: 'Qualify Lead Criteria', description: 'Preview-only workflow step; no calls are placed and no lead data is collected.' },
      { order: 3, id: 'sync-disposition-to-crm', title: 'Sync Disposition to CRM', description: 'Preview-only workflow step; no CRM write or provider transfer is performed.' },
    ],
  },
]

const USE_CASE_CATEGORIES = [
  { id: 'all', title: 'All Use Cases', slug: 'all', description: 'Browse preview-only catalog entries; live support is not verified' },
  { id: 'receptionists', title: 'Receptionists & Answering', slug: 'receptionists', description: 'Preview-only category; phone and scheduling readiness are not verified' },
  { id: 'assistants', title: 'Customer Support & Assistants', slug: 'assistants', description: 'Preview-only category; support and ticket integrations are not verified' },
  { id: 'sales', title: 'Sales & Outbound', slug: 'sales', description: 'Preview-only category; consent and carrier configuration are not verified' },
]

function voxdeskPreviewApiPlugin() {
  return {
    name: 'voxdesk-preview-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url || ''
        if (!url.startsWith('/api/') && !url.startsWith('/auth/')) {
          return next()
        }

        const sendJson = (status, payload) => {
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.setHeader('Cache-Control', 'no-store')
          res.setHeader('X-VoxDesk-Data-Mode', 'preview-fixture')
          res.end(JSON.stringify(payload))
        }

        const cleanPath = url.split('?')[0]
        const method = String(req.method || 'GET').toUpperCase()
        const sendFixtureError = (status, code, message) =>
          sendJson(status, { status: 'error', error: { code, message } })

        if (method !== 'GET') {
          return sendFixtureError(
            501,
            'preview_fixture_mutation_not_implemented',
            'Preview fixtures are read-only; this operation was not persisted or sent to a provider.',
          )
        }

        if (cleanPath.startsWith('/auth/')) {
          return sendFixtureError(
            501,
            'preview_fixture_auth_not_implemented',
            'Preview fixtures do not authenticate users or establish a session.',
          )
        }

        if (cleanPath === '/api/v1/agents' || cleanPath === '/api/agents' ||
            cleanPath.startsWith('/api/v1/agents/') || cleanPath.startsWith('/api/agents/')) {
          return sendFixtureError(
            501,
            'preview_fixture_agent_data_unavailable',
            'Preview fixtures do not create or invent agent/version records; use the authenticated API.',
          )
        }

        if (cleanPath === '/api/v1/public/analytics/summary') {
          return sendFixtureError(
            501,
            'preview_fixture_analytics_unavailable',
            'Operational analytics are not fabricated in preview fixture mode.',
          )
        }

        if (cleanPath === '/api/v1/public/voice-demo/session' ||
            cleanPath.startsWith('/api/v1/public/voice-demo/session/')) {
          return sendFixtureError(
            503,
            'voice_demo_not_configured',
            'No live voice-demo provider session was created; configure a provider to enable this operation.',
          )
        }

        if (cleanPath === '/api/v1/public/home') {
          return sendJson(200, {
            status: 'ok',
            data: {
              capabilities: [],
              use_cases: [],
              security_items: [],
              developer_features: [],
            },
            meta: {
              generated_at: new Date().toISOString(),
              registered_api_operations: 0,
              evidence_scope: 'Vite development preview fixture only; backend route registration, provider configuration, and runtime behavior were not checked.',
            },
          })
        }

        if (cleanPath === '/api/v1/public/use-cases/categories') {
          return sendJson(200, { status: 'ok', data: USE_CASE_CATEGORIES })
        }

        if (cleanPath === '/api/v1/public/use-cases') {
          const items = USE_CASES_CATALOG.map((useCase) => ({
            slug: useCase.slug,
            title: useCase.title,
            category: useCase.category,
            category_title: useCase.category_title,
            description: useCase.description,
            capabilities: useCase.capabilities,
            supported: useCase.supported,
            featured: useCase.featured,
            verified: useCase.verified,
          }))
          return sendJson(200, {
            status: 'ok',
            data: {
              items,
              categories: USE_CASE_CATEGORIES,
              total: items.length,
              page: 1,
              page_size: 12,
            },
          })
        }

        if (cleanPath.startsWith('/api/v1/public/use-cases/')) {
          const encodedSlug = cleanPath.slice('/api/v1/public/use-cases/'.length)
          let slug
          try {
            slug = decodeURIComponent(encodedSlug)
          } catch {
            return sendFixtureError(400, 'invalid_use_case_slug', 'The use-case slug is not valid URL encoding.')
          }
          if (!/^[a-z0-9-]{1,200}$/.test(slug)) {
            return sendFixtureError(400, 'invalid_use_case_slug', 'The use-case slug must contain lowercase letters, numbers, or hyphens.')
          }
          const found = USE_CASES_CATALOG.find((useCase) => useCase.slug === slug)
          if (!found) {
            return sendFixtureError(404, 'use_case_not_found', `No preview fixture is defined for use case ${slug}.`)
          }
          const detail = {
            slug: found.slug,
            title: found.title,
            category: found.category,
            category_title: found.category_title,
            description: found.description,
            capabilities: found.capabilities.map((id) => ({
              id,
              slug: id,
              title: id.split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' '),
              description: 'Capability availability is not verified in this development-only preview fixture.',
              enabled: null,
              category: found.category,
              verified: false,
            })),
            workflow: found.workflow,
            integrations: [],
            security: [],
            faq: [],
            example_conversation: [],
            supported: null,
            verified: false,
            featured: found.featured,
            meta: { evidence_scope: 'local preview catalog only; integrations and runtime behavior were not verified' },
          }
          return sendJson(200, { status: 'ok', data: detail })
        }

        return sendFixtureError(
          404,
          'preview_fixture_route_not_found',
          `No read-only preview fixture is defined for GET ${cleanPath}. The request was not sent to a provider.`,
        )
      })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const previewFixturesEnabled =
    mode === 'development' && env.VOXDESK_ENABLE_PREVIEW_API_FIXTURES === 'true'
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
  const allowedPreviewHosts = (env.VITE_ALLOWED_HOSTS || '')
    .split(',')
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean)
  const apiProxy = { target: apiProxyTarget, changeOrigin: true }

  return {
    // Hardcoded API fixtures are opt-in, dev-server-only, and labelled on every
    // response. Normal development and all production builds use the real API
    // proxy instead of returning mock identities, analytics, or success states.
    plugins: [react(), ...(previewFixturesEnabled ? [voxdeskPreviewApiPlugin()] : [])],
    server: {
      host: '0.0.0.0',
      // Vite already permits localhost and IP literals. Limit host checks to
      // the application preview domain plus explicitly configured local hosts.
      allowedHosts: ['.e2b.app', ...allowedPreviewHosts],
      proxy: {
        '/api': apiProxy,
        '/auth': apiProxy,
        '/realtime/ws': {
          target: 'http://127.0.0.1:8790',
          ws: true,
          rewrite: (path) => path.replace(/^\/realtime\/ws/, '/ws'),
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./tests/setup.js'],
      include: [
        'tests/**/*.test.{js,jsx}',
        'src/tests/**/*.test.{ts,tsx,js,jsx}',
        'src/**/__tests__/**/*.test.{ts,tsx,js,jsx}',
      ],
      exclude: ['node_modules', 'dist'],
      clearMocks: true,
      restoreMocks: true,
    },
  }
})
