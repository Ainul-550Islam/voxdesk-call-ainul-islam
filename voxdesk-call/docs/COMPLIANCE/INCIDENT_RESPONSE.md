# Incident response plan

One page, because the document you cannot read at 03:00 is the document you do
not have. Severities, who decides, what is written down, and the 72-hour GDPR
clock that starts the moment a breach is *suspected*, not when it is confirmed.

## 1. Severities

| Severity | Definition | First response | Comms |
|---|---|---|---|
| SEV-1 | Cross-tenant data exposure, auth bypass, destructive data loss, telephony outage for all tenants | 15 min, all hands, page the technical owner | Customer notice within 24 h after containment; status page within 1 h |
| SEV-2 | One tenant's data or calls degraded; privilege escalation inside a tenant; billing error affecting invoices | 1 h | Affected tenant within 24 h; status page if customer-visible |
| SEV-3 | Single feature broken, no data exposure; failed backup; elevated error rate | 1 business day | Ticket only |
| SEV-4 | Cosmetic, documentation, single-request anomaly | Next planning cycle | Ticket only |

A suspected SEV-1 is a SEV-1 until it is disproven. Downgrading requires the
technical owner's explicit sign-off recorded in the incident log.

## 2. Roles

| Role | Who | Responsibilities |
|---|---|---|
| Incident commander | Technical owner (or delegate) | Declares severity, runs the timeline, decides containment vs. evidence preservation |
| Communications | Whoever the commander names | Customer and status-page messages; one voice, no speculation |
| Scribe | Anyone not hands-on | Timestamps everything in the incident log — this becomes the post-mortem |
| Subject-matter lead | Whoever owns the failing component | Fixes it; does not talk to customers |

If only one person is available, they do all four and *say so* in the log.
Pretending a full team exists is how the timeline ends up empty.

## 3. First 30 minutes

1. **Declare** in the incident channel: severity, what is known, what is *not*
   known, who is commander. Anything suspected of exposing another tenant's data
   freezes deploys.
2. **Contain**: revoke sessions and API keys (`app/auth/identity/sessions.py`,
   `DELETE /api/service-accounts/{id}/credentials/{cid}`), disable the affected
   integration, or scale the affected worker to zero.
3. **Preserve**: copy logs and the relevant `audit_logs` rows *before* fixing
   anything. `app/audit/` is append-only for exactly this reason; the S3/object
   evidence chain is digest-pinned.
4. **Assess reach**: which tenants, which data categories, which time window.
   `/api/audit-trail/events` filtered by `event_type` is the fastest honest answer.
5. **Notify** as required by §5. Do not wait for root cause.

## 4. Investigation aids

| Question | Where to look |
|---|---|
| What happened on the API? | `audit_logs` (`app/audit/service.py`), shipped to the SIEM via `app/audit/siem_export.py` |
| Who held which permission at the time? | `/api/auth/effective-permissions?user_id=`, custom role assignments |
| Was it a role change? | `event_type` = `user.role_changed`, `custom_role.*` events |
| Did a residency rule block something? | `residency.write_refused` warnings (`app/tenancy/data_residency.py`) |
| Was a webhook forged? | `app/webhooks/` signature verification failures in the audit trail |
| Is it still running? | Health probes (`/health`, `/ready`), status page (`infra/status/`) |

## 5. Notification obligations

| Trigger | Obligation | Deadline |
|---|---|---|
| Personal data breach (GDPR Art. 33) | Notify the supervisory authority | **72 hours** from awareness |
| Same, high risk to data subjects (Art. 34) | Notify affected individuals | Without undue delay |
| HIPAA breach of unsecured PHI | Notify covered entity; they notify HHS | Per BAA — **no BAA exists today**, so no PHI should be processed |
| Contractual | Per customer agreement | Per agreement |
| Everyone else | Status page + post-mortem | Within 5 business days |

The 72-hour clock is a fact about the calendar, not about our confidence. Start
the log entry when the suspicion appears.

## 6. Post-mortem template

```
Incident:          INC-<yyyy>-<nnn>
Severity:          SEV-n (peak), SEV-m (final)
Duration:          detected <t0> → contained <t1> → resolved <t2>
Impact:            tenants affected, data categories, records, time window
Timeline:          minute-by-minute, from the scribe's log
Root cause:        the technical cause, and the change or condition that allowed it
What went well:    (be specific; vague praise teaches nothing)
What went badly:   (be specific; this is the only part that improves the system)
Action items:      owner + due date + ticket id, each one testable
Detection:         how it was found — if a customer reported it, say so plainly
```

Post-mortems are blameless and internal by default; the customer-facing summary
is a separate, shorter document.

## 7. Standing assumptions

* There is no 24/7 staffed on-call today. Detection is alerting plus customer
  reports, and the plan says so rather than describing a rotation that does not
  exist.
* The single-maintainer risk is real and is mitigated by documentation, not by
  pretending otherwise.
