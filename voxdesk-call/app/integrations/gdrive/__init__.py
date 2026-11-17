"""Google Drive knowledge source (PART 14, P14-04).

Drive is a *source* of knowledge documents, not a destination. The package
therefore exposes:

* `oauth`   -- authorization URL with PKCE, token exchange and refresh;
* `client`  -- list / export with a delta (page-token) sync, transport injected;
* `mapping` -- Drive mime types to what our knowledge pipeline can ingest.

The mapping matters: Drive returns Google Docs as a native type that has no
bytes. Exporting a Doc as `text/plain` is the only way our chunker sees it, and
`application/vnd.google-apps.folder` must be walked rather than fetched.
"""

from app.integrations.gdrive import client, mapping, oauth

__all__ = ["client", "mapping", "oauth"]
