"""Small OpenTelemetry bridge used by outbound integration surfaces."""

from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator

try:
    import importlib

    _otel_trace = importlib.import_module("opentelemetry.trace")
    _TRACER = _otel_trace.get_tracer("voxdesk")
except ImportError:  # optional dependency in the current environment
    _TRACER = None


@contextmanager
def span(name: str, **attributes: object) -> Iterator[object]:
    if _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(
                    key, str(value) if not isinstance(value, (int, float, bool)) else value
                )
        yield current


def available() -> bool:
    return _TRACER is not None
