"""SIEM / audit-export transports.

Importing this package is cheap: no HTTP client, no AWS SDK, no GCP SDK. Each
sink imports its transport library inside the module that uses it, so a tenant
with only a Splunk sink never pays for the boto3 import.
"""

from __future__ import annotations

from app.compliance_v2.siem.events import SCHEMA_VERSION, SiemEvent, from_audit_log
from app.compliance_v2.siem.sinks_base import SinkHealth, SinkResult, SiemSink

__all__ = [
    "SCHEMA_VERSION",
    "SiemEvent",
    "SiemSink",
    "SinkHealth",
    "SinkResult",
    "from_audit_log",
]
