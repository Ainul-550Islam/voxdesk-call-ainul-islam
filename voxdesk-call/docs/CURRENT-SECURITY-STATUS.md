# Current security status — scope-aware authorization

Implemented in this layer:

- Organization, tenant and environment membership are checked on the server.
  A path or body id outside the principal's organization is a 404 with the
  same body as a missing row.
- Revoked or expired membership is rejected by the existing authenticator, so
  current tenant APIs fail closed. Suspended membership keeps read permissions
  and loses writes. `is_active` is still the login kill switch and is not
  flipped by membership status.
- A caller cannot grant themselves a higher role. Granting a role still goes
  through `can_assign_role`. The last active owner cannot be removed.
- Invitation tokens are stored as SHA-256 digests. Audit rows carry the
  invitation id, not the token. A token issued for one organization cannot be
  accepted against another. Unknown, expired, revoked and cross-scope tokens
  return the same error.
- API keys and service accounts remain bound to the tenant that issued them
  and to the attributed user's membership. A disabled service account is still
  rejected by the existing credential authenticator. Scopes can only narrow.
- Production environment mutation requires the owner role. Archived and
  suspended environments cannot be selected as current.
- Quota enforcement does not treat a missing limit as zero or a malformed row
  as unlimited, and it does not block inbound calls.
- JWT claim set is unchanged. SSO, SCIM, MFA, API keys and service accounts
  are the existing implementations.

Not in this layer: environment-scoped business tables, billing hierarchy,
environment secrets, regional routing, and a second permission vocabulary.
