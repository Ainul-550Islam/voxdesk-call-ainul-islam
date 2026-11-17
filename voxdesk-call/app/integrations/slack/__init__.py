"""Slack integration (PART 14, P14-04).

Split so the security-critical part is testable without Slack:

* `oauth`  -- install URL, state, token exchange (transport injected);
* `verify` -- signing-secret verification of inbound events and the replay
              window, which is the only thing standing between a public URL and
              forged events;
* `actions`-- payload shapes for the actions the catalogue exposes.

No module here performs I/O on import, and none of them reads a client secret
from anywhere but an explicit argument.
"""

from app.integrations.slack import actions, oauth, verify

__all__ = ["actions", "oauth", "verify"]
