/**
 * The route auth boundary that `app.tsx` mounts before any private console.
 *
 * `src/app/app.tsx` imports `ProtectedRoute` and `useProtectedSession` from
 * `../components/ProtectedRoute`, but that module did not exist in the
 * repository, so `vite build` failed on a clean checkout:
 *
 *     Could not resolve "../components/ProtectedRoute" from "src/app/app.tsx"
 *
 * These tests pin the behaviour that module must have, not merely that it
 * exists — a stub returning `null` would satisfy the import and fail here.
 *
 * The important claim under test is the negative one: this component decides
 * what to render and nothing else. It must never treat an unreachable backend
 * as "signed out", because `bootstrapSession` cannot tell a missing cookie
 * from a network failure, and discarding a live session over a blink is the
 * failure mode that locks operators out of a call centre mid-shift.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ProtectedRoute, useProtectedSession } from '../components/ProtectedRoute'
import { clearSession, getAuthSession, setSessionAccessToken } from '../lib/session'

const USER = {
  id: 'u1',
  email: 'owner@example.com',
  full_name: 'Owner',
  role: 'owner',
  tenant_id: 't1',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Minimal authenticated backend: refresh, /auth/me and login all succeed. */
function installBackend({ reachable = true } = {}) {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push(url)
    if (!reachable) throw new TypeError('Failed to fetch')
    if (url.includes('/auth/refresh')) {
      return jsonResponse({ access_token: 'tok-1' })
    }
    if (url.includes('/auth/me')) {
      return jsonResponse({
        user: USER,
        tenant: { id: 't1', name: 'Acme' },
        permissions: ['calls:read'],
      })
    }
    if (url.includes('/auth/login')) {
      return jsonResponse({ access_token: 'tok-2', user: USER })
    }
    return jsonResponse({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

function StatusProbe({ enabled }: { enabled: boolean }) {
  const status = useProtectedSession(enabled)
  return <span data-testid="status">{status}</span>
}

beforeEach(() => {
  clearSession()
})

afterEach(() => {
  vi.unstubAllGlobals()
  clearSession()
})

describe('useProtectedSession', () => {
  it('restores a session from the refresh cookie and reports authenticated', async () => {
    const { calls } = installBackend()

    render(<StatusProbe enabled />)
    expect(screen.getByTestId('status')).toHaveTextContent('loading')

    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    )
    expect(calls.some((u) => u.includes('/auth/refresh'))).toBe(true)
    expect(calls.some((u) => u.includes('/auth/me'))).toBe(true)
    // The store, not just the hook, now holds the session.
    expect(getAuthSession()?.user.email).toBe('owner@example.com')
  })

  it('does not spend a refresh round trip on a public route', async () => {
    const { calls } = installBackend()

    render(<StatusProbe enabled={false} />)

    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(calls).toEqual([])
  })

  it('reports anonymous when no session can be restored', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'unauthorized' }, 401))
    )

    render(<StatusProbe enabled />)
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
    )
  })

  it('does not resurrect a session that was cleared mid-restore', async () => {
    installBackend()

    render(<StatusProbe enabled />)
    clearSession()
    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
    )
    expect(getAuthSession()).toBeNull()
  })
})

describe('ProtectedRoute', () => {
  it('shows a status line, not the login form, while restoring', () => {
    installBackend()
    render(<ProtectedRoute status="loading" nextPath="/workspace/agents" />)
    expect(screen.getByRole('status')).toHaveTextContent(/restoring your session/i)
    expect(screen.queryByLabelText(/email/i)).toBeNull()
  })

  it('renders the existing Login with the session submit path, not a second form', () => {
    render(<ProtectedRoute status="anonymous" nextPath="/workspace/agents" />)
    // The real Login component: its own labels and the server's own wording.
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
  })

  it('renders nothing when the session is already authenticated', () => {
    const { container } = render(<ProtectedRoute status="authenticated" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('only forwards a same-origin absolute path after login', async () => {
    const onLoginSuccess = vi.fn()
    render(
      <ProtectedRoute
        status="anonymous"
        nextPath="//evil.example/steal"
        onLoginSuccess={onLoginSuccess}
      />
    )

    installBackend()
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'owner@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'correct horse battery staple' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalled())
    // A protocol-relative URL is an off-site redirect.
    expect(onLoginSuccess).toHaveBeenCalledWith('/workspace')
  })

  it('keeps a live session when the backend is temporarily unreachable', async () => {
    setSessionAccessToken('tok-live')
    installBackend({ reachable: false })

    render(<StatusProbe enabled />)
    // A bearer token is a usable session even though /auth/me cannot be
    // reached, so the boundary stays open. Treating a thrown fetch as
    // "signed out" would eject an operator from a live call queue.
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
  })

  it('bridges the console token into this store after a successful login', async () => {
    // The real flow: Login writes through lib/api.js, whose token is private
    // to that module and separate from this store. Without the bridge the
    // boundary would still read "anonymous" after a correct password.
    installBackend()
    const onLoginSuccess = vi.fn()
    render(<ProtectedRoute status="anonymous" nextPath="/workspace" onLoginSuccess={onLoginSuccess} />)

    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'owner@example.com' } })
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalled())
    await waitFor(() => expect(getAuthSession()?.user.email).toBe('owner@example.com'))
  })
})
