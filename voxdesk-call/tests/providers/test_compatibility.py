from __future__ import annotations
import inspect
from types import SimpleNamespace
from app.providers import compatibility


class Options:
    __dataclass_fields__ = {name: object() for name in ("encoding", "sample_rate", "language", "model", "interim_results")}


def test_deepgram_detection_requires_both_sdk_and_pipecat_contract(monkeypatch):
    monkeypatch.setattr(compatibility.metadata, "version", lambda name: "4.7.0")
    def load_module(name):
        if name == "deepgram":
            return SimpleNamespace(LiveOptions=Options)
        if name == "pipecat.services.deepgram.stt":
            return SimpleNamespace(DeepgramSTTService=lambda live_options: None)
        if name == "websockets":
            return SimpleNamespace(connect=lambda uri, *, extra_headers=None: None)
        raise ImportError(name)
    monkeypatch.setattr(compatibility, "import_module", load_module)
    report = compatibility.detect_deepgram()
    assert report.available
    assert {"streaming", "interim_results"} <= report.capabilities


def test_websocket_header_keyword_tracks_callable_signature():
    def legacy_connect(uri, *, extra_headers=None):
        return None

    def modern_connect(uri, *, additional_headers=None):
        return None

    assert compatibility.websocket_header_keyword(legacy_connect) == "extra_headers"
    assert compatibility.websocket_header_keyword(modern_connect) == "additional_headers"


def test_websocket_incompatible_surface_fails_closed():
    def connect(uri):
        return None

    try:
        compatibility.websocket_header_keyword(connect)
    except compatibility.ProviderCompatibilityError:
        pass
    else:
        raise AssertionError("incompatible WebSocket client must not be reported as usable")


def test_sdk_import_failure_is_explicit_unavailable_not_success(monkeypatch):
    monkeypatch.setattr(compatibility.metadata, "version", lambda name: "4.7.0")
    def fail(name): raise ImportError("hidden detail")
    monkeypatch.setattr(compatibility, "import_module", fail)
    report = compatibility.detect_deepgram()
    assert not report.available
    assert report.reason == "incompatible SDK surface: ImportError"
