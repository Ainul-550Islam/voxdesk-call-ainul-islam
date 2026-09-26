"""Enterprise lead lifecycle.

``app.db.models.Lead`` remains the canonical person record. This package adds
lifecycle, deduplication, scoring, segmentation, import/export, activities,
tasks and consent around that row. It does not define a Contact, a second CRM,
a second dialer, or a second do-not-call list.
"""
