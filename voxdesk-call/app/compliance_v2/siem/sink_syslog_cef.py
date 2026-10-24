"""RFC 5424 syslog sink carrying ArcSight CEF payloads.

Framing and escaping are the two places a home-grown syslog sender breaks:

* **RFC 5424 header** — ``<PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID
  MSGID STRUCTURED-DATA MSG``. The structured-data element carries the tenant
  and event id, so a collector can group and de-duplicate without parsing the
  CEF body.
* **CEF escaping** — in the header, ``|`` and ``\\`` are escaped; in the
  extension, ``=``, ``|``, ``\\`` and newlines are escaped. A summary that
  contains a pipe (very common in phone numbers and JSON snippets) silently
  corrupts the event if this is skipped.

Transport is TCP or TLS with newline-delimited messages. TCP is a stream, so a
write only proves the bytes reached the socket — that is stated plainly rather
than presented as end-to-end delivery proof.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl

from app.compliance_v2.siem.events import SiemEvent
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_TIMEOUT,
    CATEGORY_TRANSPORT,
    SiemSink,
    SinkHealth,
    SinkNotConfiguredError,
    SinkResult,
)

#: Syslog severity used for every audit record: 5 = notice.
_SYSLOG_PRI = 13  # facility 1 (user) * 8 + severity 5 (notice)
_CEF_VERSION = "0"
_DEVICE_VENDOR = "VoxDesk"
_DEVICE_PRODUCT = "VoxDesk"
_DEVICE_VERSION = "1.0"

_SEVERITY_BY_RESULT = {
    "success": "3",
    "failure": "7",
    "denied": "8",
    "error": "7",
    "pending": "4",
}


def cef_escape_header(value: str) -> str:
    """Escape a CEF header field (backslash and pipe)."""
    return (value or "").replace("\\", "\\\\").replace("|", "\\|")


def cef_escape_extension(value: str) -> str:
    """Escape a CEF extension value (backslash, equals, pipe, newlines)."""
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("|", "\\|")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def to_cef(event: SiemEvent) -> str:
    """Render one audit event as a CEF message (no syslog header)."""
    name = cef_escape_header(event.event_type or "audit_event")
    severity = _SEVERITY_BY_RESULT.get(event.result, "5")
    extensions = [
        ("rt", event.occurred_at),
        ("externalId", event.event_id),
        ("suser", event.actor_email or event.actor_id),
        ("act", event.action),
        ("outcome", event.result),
        ("cs1Label", "tenantId"),
        ("cs1", event.tenant_id),
        ("cs2Label", "resourceType"),
        ("cs2", event.resource_type),
        ("cs3Label", "resourceId"),
        ("cs3", event.resource_id),
        ("cs4Label", "requestId"),
        ("cs4", event.request_id),
        ("src", event.ip_address),
        ("dvchost", "voxdesk"),
        ("msg", event.canonical_json()),
    ]
    body = " ".join(f"{key}={cef_escape_extension(value)}" for key, value in extensions if value != "")
    return (
        f"CEF:{_CEF_VERSION}|{cef_escape_header(_DEVICE_VENDOR)}|{cef_escape_header(_DEVICE_PRODUCT)}"
        f"|{cef_escape_header(_DEVICE_VERSION)}|{name}|{name}|{severity}|{body}"
    )


def to_rfc5424(event: SiemEvent, *, hostname: str = "voxdesk") -> str:
    """Render one audit event as a complete RFC 5424 message."""
    timestamp = event.occurred_at
    structured = (
        f'[voxdesk@32473 tenantId="{cef_escape_header(event.tenant_id)}" '
        f'eventId="{cef_escape_header(event.event_id)}" '
        f'schemaVersion="{cef_escape_header(event.schema_version)}"]'
    )
    return (
        f"<{_SYSLOG_PRI}>1 {timestamp} {hostname} voxdesk-audit - {event.event_type or 'audit'} "
        f"{structured} {to_cef(event)}"
    )


class SyslogCefSink(SiemSink):
    """Streams CEF over RFC 5424 to a TCP or TLS collector."""

    kind = "syslog_cef"

    def __init__(
        self,
        *,
        host: str,
        port: int = 6514,
        use_tls: bool = True,
        ca_file: str = "",
        verify_tls: bool = True,
        timeout: float = 10.0,
        connect=None,
    ) -> None:
        if not (host or "").strip():
            raise SinkNotConfiguredError("syslog collector host is empty")
        if not (0 < int(port) < 65536):
            raise SinkNotConfiguredError("syslog collector port is out of range")
        self._host = host.strip()
        self._port = int(port)
        self._use_tls = use_tls
        self._ca_file = ca_file
        self._verify = verify_tls
        self._timeout = timeout
        #: Injected by tests that speak the protocol on a local socket.
        self._connect = connect

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self._use_tls:
            return None
        context = ssl.create_default_context(cafile=self._ca_file or None)
        if not self._verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    async def _open(self):
        if self._connect is not None:
            return await self._connect(self._host, self._port, self._ssl_context())
        return await asyncio.open_connection(
            self._host, self._port, ssl=self._ssl_context(), server_hostname=self._host if self._use_tls else None
        )

    async def send_batch(self, events: list[SiemEvent]) -> SinkResult:
        if not events:
            return SinkResult.success(0)
        payload = b"".join(f"{to_rfc5424(event)}\n".encode("utf-8") for event in events)
        writer = None
        try:
            reader, writer = await asyncio.wait_for(self._open(), timeout=self._timeout)
            del reader
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
        except asyncio.TimeoutError:
            return SinkResult.retry(CATEGORY_TIMEOUT, "syslog collector did not accept the batch in time")
        except (OSError, ssl.SSLError) as exc:
            return SinkResult.retry(
                CATEGORY_TRANSPORT, f"syslog connection failed: {type(exc).__name__}: {exc}"
            )
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=self._timeout)
                except (asyncio.TimeoutError, OSError, ssl.SSLError):
                    writer.transport.abort() if hasattr(writer, "transport") and writer.transport else None
        return SinkResult.success(
            len(events),
            detail={"transport": "tls" if self._use_tls else "tcp", "frames": len(events)},
        )

    async def health(self) -> SinkHealth:
        checked = dt.datetime.now(dt.timezone.utc).isoformat()
        writer = None
        try:
            _reader, writer = await asyncio.wait_for(self._open(), timeout=self._timeout)
            return SinkHealth(ok=True, detail="collector accepted a connection", checked_at=checked)
        except (asyncio.TimeoutError, OSError, ssl.SSLError) as exc:
            return SinkHealth(
                ok=False, detail=f"{type(exc).__name__}: {exc}", checked_at=checked
            )
        finally:
            if writer is not None:
                writer.close()


__all__ = [
    "SyslogCefSink",
    "cef_escape_extension",
    "cef_escape_header",
    "to_cef",
    "to_rfc5424",
]
