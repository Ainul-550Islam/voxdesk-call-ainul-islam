/**
 * dashboard/src/pages/public/SignupPage.tsx
 * Real workspace + owner account signup page with open-redirect-safe return path (`next`).
 */

import React, { useMemo, useState } from 'react';
import {
  persistAuthenticatedSession,
  PublicSiteApiError,
  sanitizeReturnPath,
  signupWorkspace,
} from '../../api/public-site';

export interface SignupPageProps {
  nextPath?: string | null;
  onSignupSuccess?: (redirectTo: string) => void;
}

export function SignupPage({ nextPath, onSignupSuccess }: SignupPageProps) {
  const [organizationName, setOrganizationName] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [industry, setIndustry] = useState('healthcare');
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
    if (!organizationName.trim() || !fullName.trim() || !email.trim() || !password) {
      setErrorMessage('All fields are required to create a workspace.');
      return;
    }
    if (password.length < 8) {
      setErrorMessage('Password must be at least 8 characters long.');
      return;
    }

    setSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await signupWorkspace({
        organization_name: organizationName.trim(),
        full_name: fullName.trim(),
        email: email.trim(),
        password,
        industry,
        next_path: safeNext,
      });

      persistAuthenticatedSession({
        accessToken: res.access_token,
        user: res.user,
      });

      const targetPath = sanitizeReturnPath(res.redirect_to || safeNext, '/app/overview');
      if (onSignupSuccess) {
        onSignupSuccess(targetPath);
      } else if (typeof window !== 'undefined') {
        window.location.assign(targetPath);
      }
    } catch (err) {
      if (err instanceof PublicSiteApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage(
          err instanceof Error ? err.message : 'Unable to create workspace.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="public-signup-page"
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
          maxWidth: 460,
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
            Create Your VoxDesk Workspace
          </h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: '#94A3B8' }}>
            Provision an isolated tenant workspace with durable agent versioning.
          </p>
        </div>

        {errorMessage && (
          <div
            data-testid="signup-error-alert"
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
          data-testid="signup-form"
          style={{ display: 'flex', flexDirection: 'column', gap: 13 }}
        >
          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}>
              Organization / Company Name
            </label>
            <input
              type="text"
              data-testid="signup-org-input"
              value={organizationName}
              onChange={(e) => setOrganizationName(e.target.value)}
              placeholder="Acme Health Systems"
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
            <label style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}>
              Your Full Name
            </label>
            <input
              type="text"
              data-testid="signup-name-input"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Dr. Sarah Chen"
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
            <label style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}>
              Work Email
            </label>
            <input
              type="email"
              data-testid="signup-email-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="sarah@acmehealth.com"
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
            <label style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}>
              Primary Industry
            </label>
            <select
              data-testid="signup-industry-select"
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              style={{
                width: '100%',
                padding: '10px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F8FAFC',
                fontSize: 13.5,
              }}
            >
              <option value="healthcare">Healthcare & Clinics</option>
              <option value="dental">Dental Practices</option>
              <option value="legal">Legal & Professional Services</option>
              <option value="home_services">Home & Field Services</option>
              <option value="financial">Financial & Insurance</option>
              <option value="general">Enterprise / General</option>
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}>
              Password (minimum 8 characters)
            </label>
            <input
              type="password"
              data-testid="signup-password-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Create a strong password"
              required
              minLength={8}
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
            data-testid="signup-submit-btn"
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
            {submitting ? 'Provisioning Workspace...' : 'Create Workspace'}
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
          <span>Already have a workspace?</span>
          <a
            href={
              requestedNext
                ? `/login?next=${encodeURIComponent(safeNext)}`
                : '/login'
            }
            style={{ color: '#60A5FA', textDecoration: 'none', fontWeight: 600 }}
          >
            Sign in →
          </a>
        </div>
      </div>
    </div>
  );
}

export default SignupPage;
