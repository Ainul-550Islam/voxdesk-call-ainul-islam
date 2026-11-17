"""Make.com integration package (PART 14, P14-04).

Two directions, both as data:

* **export** -- turn a VoxDesk workflow into a Make blueprint a customer can
  import into their own Make account;
* **import** -- map a Make blueprint's modules back onto our preset functions,
  refusing anything we cannot execute instead of importing a scenario that will
  silently do nothing.

A blueprint is JSON; no HTTP call is made from this package. The connection
itself (Make custom app, webhook URL) is configured by the tenant.

This package lives at `integrations/make/` beside `integrations/zapier/` and
`integrations/n8n/`, because it is a *published integration*, not part of the
server: it is imported by `blueprint_test.py` and by anyone vendoring the folder,
never by `app/`.
"""

from . import blueprint

__all__ = ["blueprint"]
