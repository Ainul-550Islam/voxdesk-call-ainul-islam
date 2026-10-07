/**
 * dashboard/src/pages/public/LoginPage.tsx
 * Real backend-wired Login page with open-redirect-safe return path (`next`) validation.
 */

import React, { useMemo, useState } from 'react';
import {
  loginWithPassword,
  persistAuthenticatedSession,
  PublicSiteApiError,
  sanitizeReturnPath,
} from '../../api/public-site';

export interface LoginPageProps {
  nextPath?: string | null;
  onLoginSuccess?: (redirectTo: string) => void;
}

export function LoginPage({ nextPath, onLoginSuccess }: LoginPageProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const requestedNext = useMemo(() => {
    if (nextPath) return nextPath;
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      return params.get('next');
    }
    return null;
  }, [nextPath]);

  const safeNext = useMemo(
    () => sanitizeReturnPath(requestedNext, '/app/overview'),
    [requestedNext],
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setErrorMessage('Please enter both your work email and password.');
      return;
    }

    setSubmitting(true);
    setErrorMessage(null);
    try {
      const response = await loginWithPassword({
        email: trimmedEmail,
        password,
        next_path: safeNext,
      });

      if (response.mfa_required) {
        setErrorMessage(
          'Multi-factor verification is required for this account. Complete MFA verification to continue.',
        );
        return;
      }

      if (!response.access_token) {
        setErrorMessage('Authentication did not return an access token.');
        return;
      }

      persistAuthenticatedSession({
        accessToken: response.access_token,
        user: response.user || null,
      });

      const targetPath = sanitizeReturnPath(
        response.redirect_to || safeNext,
        '/app/overview',
      );
      if (onLoginSuccess) {
        onLoginSuccess(targetPath);
      } else if (typeof window !== 'undefined') {
        window.location.assign(targetPath);
      }
    } catch (err) {
      if (err instanceof PublicSiteApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage(
          err instanceof Error ? err.message : 'Unable to sign in. Please try again.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="public-login-page"
      style={{
        minHeight: '100vh',
        background: '#06090F',
        color: '#F1F5F9',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 24,
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 420,
          background: '#0F1623',
          border: '1px solid #1E2D45',
          borderRadius: 16,
          padding: 28,
          boxShadow: '0 20px 50px rgba(0,0,0,0.45)',
        }}
      >
        <div style={{ marginBottom: 20 }}>
          <a
            href="/"
            style={{
              fontSize: 12,
              color: '#94A3B8',
              textDecoration: 'none',
              display: 'inline-block',
              marginBottom: 12,
            }}
          >
            ← Back to Public Website
          </a>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#F8FAFC' }}>
            Sign in to VoxDesk
          </h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: '#94A3B8' }}>
            Access your private AI voice & chat agent workspace.
          </p>
        </div>

        {requestedNext && (
          <div
            data-testid="login-return-path-notice"
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              background: 'rgba(59,130,246,0.1)',
              border: '1px solid rgba(59,130,246,0.3)',
              color: '#93C5FD',
              fontSize: 12,
              marginBottom: 14,
            }}
          >
            After signing in, you will continue to <code>{safeNext}</code>
          </div>
        )}

        {errorMessage && (
          <div
            data-testid="login-error-alert"
            role="alert"
            style={{
              padding: '10px 12px',
              borderRadius: 8,
              background: 'rgba(239,68,68,0.12)',
              border: '1px solid rgba(239,68,68,0.35)',
              color: '#FCA5A5',
              fontSize: 12.5,
              marginBottom: 14,
            }}
          >
            {errorMessage}
          </div>
        )}

        <form
          onSubmit={(e) => void handleSubmit(e)}
          data-testid="login-form"
          style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
        >
          <div>
            <label
              htmlFor="login-email"
              style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 6 }}
            >
              Work Email
            </label>
            <input
              id="login-email"
              type="email"
              data-testid="login-email-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="email"
              required
              style={{
                width: '100%',
                padding: '10px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F8FAFC',
                fontSize: 13.5,
              }}
            />
          </div>

          <div>
            <label
              htmlFor="login-password"
              style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 6 }}
            >
              Password
            </label>
            <input
              id="login-password"
              type="password"
              data-testid="login-password-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              autoComplete="current-password"
              required
              style={{
                width: '100%',
                padding: '10px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F8FAFC',
                fontSize: 13.5,
              }}
            />
          </div>

          <button
            type="submit"
            data-testid="login-submit-btn"
            disabled={submitting}
            style={{
              marginTop: 4,
              padding: '11px 16px',
              borderRadius: 8,
              border: 'none',
              background: '#2563EB',
              color: '#FFFFFF',
              fontSize: 13.5,
              fontWeight: 600,
              cursor: submitting ? 'wait' : 'pointer',
            }}
          >
            {submitting ? 'Signing in...' : 'Sign In to Workspace'}
          </button>
        </form>

        <div
          style={{
            marginTop: 18,
            paddingTop: 14,
            borderTop: '1px solid #1E2D45',
            fontSize: 12.5,
            color: '#94A3B8',
            display: 'flex',
            justifyContent: 'space-between',
          }}
        >
          <span>New to VoxDesk?</span>
          <a
            href={
              requestedNext
                ? `/signup?next=${encodeURIComponent(safeNext)}`
                : '/signup'
            }
            style={{ color: '#60A5FA', textDecoration: 'none', fontWeight: 600 }}
          >
            Create a workspace →
          </a>
        </div>
      </div>
    </div>
  );
}

export default LoginPage;
