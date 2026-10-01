"use client";

import { useEffect, useState } from "react";
import { api, ApiError, ReviewCase } from "@/lib/api";

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<ReviewCase[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.reviewCases()
      .then((rows) => { if (!cancelled) setReviews(rows); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load reviews"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div className="loading">Loading reviews…</div>;
  if (error) return <div className="error-banner">{error}</div>;

  return (
    <>
      <header className="page-head">
        <h1>Review Queue — Human Approval (P0-05)</h1>
        <p className="muted">Backend: app/review/service.py — review_required outputs from specialized agents require qualified human review</p>
      </header>
      <section className="card">
        <h2>Cases ({reviews.length})</h2>
        {reviews.length === 0 ? <p className="muted">No cases. Execute a specialized agent that requires review to create one.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>Subject</th><th>Status</th><th>Priority</th><th>Created</th></tr></thead>
              <tbody>
                {reviews.map((r) => (
                  <tr key={r.id}><td><code>{r.id.slice(0,8)}</code></td><td>{r.subject_type}</td><td>{r.status}</td><td>{r.priority}</td><td>{new Date(r.created_at).toLocaleString()}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
