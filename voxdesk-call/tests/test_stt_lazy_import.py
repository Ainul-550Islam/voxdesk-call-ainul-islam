"""Regression tests for import-light speech configuration paths."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_isolated_python(script: str) -> subprocess.CompletedProcess[str]:
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_importing_stt_configuration_does_not_import_provider_sdks():
    """Queue/configuration code must not initialize Deepgram or Pipecat SDKs.

    The subprocess makes this independent of SDK modules another test may
    already have imported in the pytest worker. In particular, importing
    ``app.agent.stt`` must remain safe for ``telephony.transcription.enqueue``;
    the live-provider SDK is loaded only when a live service is explicitly
    constructed.
    """
    script = "\n".join(
        (
            "import sys",
            "from app.agent.stt import validate_stt_config",
            "assert callable(validate_stt_config)",
            "provider_modules = sorted(",
            "    name for name in sys.modules",
            "    if name == 'deepgram' or name.startswith('deepgram.')",
            "    or name == 'pipecat' or name.startswith('pipecat.')",
            ")",
            "assert not provider_modules, provider_modules",
        )
    )
    result = _run_isolated_python(script)
    assert result.returncode == 0, (
        "STT configuration import loaded a provider SDK or failed: "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


def test_legacy_stt_service_exports_resolve_lazily_on_demand():
    """Resolve compatible exports, or fail with the typed SDK error."""
    script = "\n".join(
        (
            "import sys",
            "import app.agent.stt as stt",
            "from app.providers.errors import ProviderCompatibilityError, ProviderDependencyMissingError",
            "provider_modules = [name for name in sys.modules if name == 'deepgram' or name.startswith('deepgram.') or name == 'pipecat' or name.startswith('pipecat.')]",
            "assert not provider_modules, provider_modules",
            "try:",
            "    DeepgramSTTService = stt.DeepgramSTTService",
            "    LiveOptions = stt.LiveOptions",
            "except (ProviderCompatibilityError, ProviderDependencyMissingError):",
            "    pass",
            "else:",
            "    from deepgram import LiveOptions as RuntimeLiveOptions",
            "    from pipecat.services.deepgram.stt import DeepgramSTTService as RuntimeService",
            "    assert DeepgramSTTService is RuntimeService",
            "    assert LiveOptions is RuntimeLiveOptions",
        )
    )
    result = _run_isolated_python(script)
    assert result.returncode == 0, (
        "Lazy STT compatibility exports did not resolve truthfully: "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
