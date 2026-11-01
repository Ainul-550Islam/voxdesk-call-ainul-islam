/**
 * dashboard/src/components/ProtectedRoute.tsx
 *
 * The auth boundary that `src/app/app.tsx` mounts before any private console.
 *
 * This file was missing from the repository while `app.tsx` imported it, so
 * `npm run build` failed on a clean checkout with:
 *
 *     Could not resolve "../components/ProtectedRoute" from "src/app/app.tsx"
 *
 * Everything it needs already existed and is reused here rather than
 * duplicated: the single-flight session store in `lib/session.ts` (cookie
 * refresh, `GET /auth/me`, generation guards) and the existing `Login`
 * component. Nothing in this file is a second session implementation.
 *
 * What this component does NOT do: authorise anything. `status` only decides
 * what to render. Every request still carries the bearer token and the server
 * still enforces JWT policy and RBAC on each route — a client-side gate that
 * claimed otherwise would be the vulnerability, not the control.
 */
import { useCallback, useEffect, useState } from 'react'

import Login from './Login'
import { getAccessToken as getConsoleAccessToken } from '../lib/api'
import {
  bootstrapSession,
  getAccessToken,
  getAuthSession,
  setSessionAccessToken,
  subscribeSession,
} from '../lib/session'

export type ProtectedSessionStatus = 'loading' | 'authenticated' | 'anonymous' | 'error'

/**
 * Is there a usable session?
 *
 * Token presence, not `/auth/me` completeness, decides this — the same rule
 * `app.tsx` documents ("token presence is only a UX gate") and the same rule
 * `App.jsx` already applies. Requiring the full `/auth/me` payload here would
 * lock an operator out of the console whenever that one request is slow or
 * failing, even though every API call would still succeed.
 */
function hasSession(): boolean {
  return Boolean(getAuthSession() || getAccessToken())
}

/**
 * Track the session for a route that requires authentication.
 *
 * Returns `'authenticated'` as soon as the store holds a session, so a
 * client-side navigation does not flash the login form. `enabled=false`
 * (a public route) short-circuits to `'authenticated'` and never triggers a
 * restore, because a public page must not spend a refresh-cookie round trip.
 *
 * A failed restore is `'anonymous'`, not `'error'`: `bootstrapSession` returns
 * null both for "no cookie" and for "offline", and neither is proof that a
 * session ended. Rendering the login form is recoverable; throwing away a
 * working session because the network blinked is not.
 */
export function useProtectedSession(enabled: boolean): ProtectedSessionStatus {
  const [status, setStatus] = useState<ProtectedSessionStatus>(() => {
    if (!enabled) return 'authenticated'
    return hasSession() ? 'authenticated' : 'loading'
  })

  useEffect(() => {
    if (!enabled) {
      setStatus('authenticated')
      return
    }

    let cancelled = false
    const apply = () => {
      if (!cancelled) setStatus(hasSession() ? 'authenticated' : 'anonymous')
    }

    if (hasSession()) {
      setStatus('authenticated')
    } else {
      setStatus('loading')
      void bootstrapSession()
        .then(apply)
        .catch(apply)
    }

    // A login or logout anywhere in the app moves this boundary with it.
    const unsubscribe = subscribeSession(apply)
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [enabled])

  return status
}

interface ProtectedRouteProps {
  status: ProtectedSessionStatus
  /** Where to send the user after a successful sign-in. */
  nextPath?: string
  onLoginSuccess?: (redirectTo: string) => void
}

/** Only same-origin absolute paths are ever used as a redirect target. */
function safeNextPath(nextPath: string | undefined): string {
  if (!nextPath) return '/workspace'
  if (!nextPath.startsWith('/') || nextPath.startsWith('//')) return '/workspace'
  return nextPath
}

export function ProtectedRoute({ status, nextPath, onLoginSuccess }: ProtectedRouteProps) {
  const handleSuccess = useCallback(async () => {
    // `Login` posts through `lib/api.js`, which keeps its own module-private
    // token. Mirror it into the session store this boundary reads, then load
    // `/auth/me`; without this the boundary would stay closed after a correct
    // password and the user would be shown the login form again.
    const consoleToken = getConsoleAccessToken()
    if (consoleToken) {
      setSessionAccessToken(consoleToken)
      void bootstrapSession()
    }
    onLoginSuccess?.(safeNextPath(nextPath))
  }, [nextPath, onLoginSuccess])

  if (status === 'loading') {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          minHeight: '100vh',
          display: 'grid',
          placeItems: 'center',
          background: '#f9fafb',
          fontFamily: 'system-ui',
          color: '#6b7280',
          fontSize: 14,
        }}
      >
        Restoring your session…
      </div>
    )
  }

  if (status === 'authenticated') return null

  // No secondary "sign in as someone else" action: this branch is only
  // reached when there is no session, so there is nothing to clear, and a
  // second control labelled like the form's submit button is an accessibility
  // ambiguity for no benefit.
  return <Login onSuccess={handleSuccess} />
}

export default ProtectedRoute
