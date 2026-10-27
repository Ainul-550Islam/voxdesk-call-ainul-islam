"""White-label: custom domains, certificates, branding, sender domains, widget.

Two rules shape this package:

* **A hostname resolves to at most one tenant, ever.** :mod:`domains` claims the
  domain globally (a unique constraint, not a per-tenant check) and hands out an
  ownership token whose plaintext exists once. Unknown hosts therefore cannot
  fall back to a default tenant; :func:`app.platform_v2.whitelabel.domains.resolve_host`
  returns ``None`` and the edge refuses.
* **Nothing is trusted because it was typed in.** DNS is checked against multiple
  resolvers and must agree; the certificate exists only after the domain is
  ACTIVE; email sending is off until SPF, DKIM and DMARC all pass.
"""

from __future__ import annotations

__all__: list[str] = []
