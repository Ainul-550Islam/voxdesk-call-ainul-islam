/**
 * dashboard/src/pages/public/ContactSalesPage.tsx
 * Public Contact Sales page backed by durable PostgreSQL persistence (POST /api/v1/public/contact-sales).
 */

import React, { useState } from 'react';
import { PublicSiteApiError, submitPublicContactSales } from '../../api/public-site';
import type { PublicContactSalesResponse } from '../../api/types/public-widget';

export function ContactSalesPage() {
  const [fullName, setFullName] = useState('');
  const [workEmail, setWorkEmail] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [monthlyCallVolume, setMonthlyCallVolume] = useState('10,000 - 50,000 mins/mo');
  const [primaryUseCase, setPrimaryUseCase] = useState('Patient Intake & Scheduling');
  const [message, setMessage] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<PublicContactSalesResponse | null>(
    null,
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fullName.trim() || !workEmail.trim() || !companyName.trim()) {
      setErrorMessage('Full name, work email, and company name are required.');
      return;
    }

    setSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await submitPublicContactSales({
        full_name: fullName.trim(),
        work_email: workEmail.trim(),
        company_name: companyName.trim(),
        job_title: jobTitle.trim(),
        phone_number: phoneNumber.trim() || undefined,
        monthly_call_volume: monthlyCallVolume,
        primary_use_case: primaryUseCase,
        message: message.trim(),
        source_path:
          typeof window !== 'undefined' ? window.location.pathname : '/contact-sales',
      });
      setConfirmation(res);
    } catch (err) {
      if (err instanceof PublicSiteApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage(
          err instanceof Error
            ? err.message
            : 'Unable to submit inquiry. Please try again.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="public-contact-sales-page"
      style={{
        minHeight: '100vh',
        background: '#06090F',
        color: '#F1F5F9',
        padding: '48px 24px',
      }}
    >
      <div
        style={{
          maxWidth: 720,
          margin: '0 auto',
          background: '#0F1623',
          border: '1px solid #1E2D45',
          borderRadius: 16,
          padding: 32,
          boxShadow: '0 20px 50px rgba(0,0,0,0.45)',
        }}
      >
        <div style={{ marginBottom: 24 }}>
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
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, color: '#F8FAFC' }}>
            Contact Enterprise Sales
          </h1>
          <p style={{ margin: '8px 0 0', fontSize: 14, color: '#94A3B8', lineHeight: 1.5 }}>
            Discuss HIPAA BAA requirements, custom SIP trunking, dedicated concurrency SLAs,
            and enterprise multi-tenant deployment.
          </p>
        </div>

        {confirmation ? (
          <div
            data-testid="contact-sales-confirmation"
            style={{
              padding: 20,
              borderRadius: 12,
              background: 'rgba(16,185,129,0.12)',
              border: '1px solid rgba(16,185,129,0.4)',
              color: '#D1FAE5',
            }}
          >
            <h2 style={{ margin: '0 0 8px', fontSize: 18, color: '#34D399' }}>
              Inquiry Received
            </h2>
            <p style={{ margin: '0 0 12px', fontSize: 13.5, lineHeight: 1.5 }}>
              {confirmation.confirmation_message}
            </p>
            <div style={{ fontSize: 12, color: '#A7F3D0' }}>
              Reference ID: <code>{confirmation.id}</code> · Organization:{' '}
              <strong>{confirmation.company_name}</strong>
            </div>
          </div>
        ) : (
          <form
            onSubmit={(e) => void handleSubmit(e)}
            data-testid="contact-sales-form"
            style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            {errorMessage && (
              <div
                data-testid="contact-sales-error"
                role="alert"
                style={{
                  padding: '10px 12px',
                  borderRadius: 8,
                  background: 'rgba(239,68,68,0.12)',
                  border: '1px solid rgba(239,68,68,0.35)',
                  color: '#FCA5A5',
                  fontSize: 12.5,
                }}
              >
                {errorMessage}
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div>
                <label
                  style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
                >
                  Full Name *
                </label>
                <input
                  type="text"
                  data-testid="contact-name-input"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Alex Rivera"
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
                  style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
                >
                  Work Email *
                </label>
                <input
                  type="email"
                  data-testid="contact-email-input"
                  value={workEmail}
                  onChange={(e) => setWorkEmail(e.target.value)}
                  placeholder="alex@enterprise.com"
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
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div>
                <label
                  style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
                >
                  Company Name *
                </label>
                <input
                  type="text"
                  data-testid="contact-company-input"
                  value={companyName}
                  onChange={(e) => setCompanyName(e.target.value)}
                  placeholder="NorthStar Health Group"
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
                  style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
                >
                  Job Title
                </label>
                <input
                  type="text"
                  data-testid="contact-job-input"
                  value={jobTitle}
                  onChange={(e) => setJobTitle(e.target.value)}
                  placeholder="VP of Patient Operations"
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
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div>
                <label
                  style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
                >
                  Estimated Monthly Call Volume
                </label>
                <select
                  data-testid="contact-volume-select"
                  value={monthlyCallVolume}
                  onChange={(e) => setMonthlyCallVolume(e.target.value)}
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
                  <option value="Under 10,000 mins/mo">Under 10,000 mins/mo</option>
                  <option value="10,000 - 50,000 mins/mo">10,000 - 50,000 mins/mo</option>
                  <option value="50,000 - 250,000 mins/mo">50,000 - 250,000 mins/mo</option>
                  <option value="250,000+ mins/mo">250,000+ mins/mo</option>
                </select>
              </div>

              <div>
                <label
                  style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
                >
                  Primary Use Case
                </label>
                <input
                  type="text"
                  data-testid="contact-usecase-input"
                  value={primaryUseCase}
                  onChange={(e) => setPrimaryUseCase(e.target.value)}
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
            </div>

            <div>
              <label
                style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
              >
                Phone Number (Optional)
              </label>
              <input
                type="tel"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="+1 (555) 234-5678"
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
                style={{ display: 'block', fontSize: 12, color: '#CBD5E1', marginBottom: 5 }}
              >
                Project Requirements & Integration Notes
              </label>
              <textarea
                data-testid="contact-message-textarea"
                rows={4}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Tell us about your telephony carriers, EHR/CRM integrations, and compliance requirements..."
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
              data-testid="contact-sales-submit-btn"
              disabled={submitting}
              style={{
                marginTop: 4,
                padding: '12px 18px',
                borderRadius: 8,
                border: 'none',
                background: '#2563EB',
                color: '#FFFFFF',
                fontSize: 14,
                fontWeight: 600,
                cursor: submitting ? 'wait' : 'pointer',
              }}
            >
              {submitting ? 'Submitting Inquiry...' : 'Submit Enterprise Inquiry'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default ContactSalesPage;
