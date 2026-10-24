"""Live proof harness: real PSTN calls, real providers, auditable evidence.

Four pieces, each with one job:

* :mod:`~app.telephony.live_proof.cost_guard` -- hard minutes/spend ceiling that
  aborts a run before it can cost more than the operator allowed;
* :mod:`~app.telephony.live_proof.pstn_probe` -- places the real call with the
  carrier, waits for the live pipeline to finish, and reads the measurements the
  pipeline itself persisted;
* :mod:`~app.telephony.live_proof.evidence_writer` -- writes the hash-stamped
  evidence pair, and refuses to write anything whose provider ledger cannot
  support its own claim;
* :mod:`app.api.live_proof_routes` -- the operator-facing HTTP surface, gated by
  ``require_permission`` and tenant scoped.

The package never imports the probe's carrier SDK at import time, so importing it
stays cheap and works in environments with no provider credentials.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.telephony.live_proof.cost_guard import (
    CostGuard,
    CostGuardExceeded,
    CostReport,
)
from app.telephony.live_proof.evidence_writer import (
    REQUIRED_STAGES,
    EvidenceBundle,
    EvidenceIntegrityError,
    ProviderCall,
    ProviderCallLedger,
    TurnMeasurement,
    assert_all_real,
    percentile,
    runtime_info,
    summarise,
    write_evidence,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.telephony.live_proof.pstn_probe import LiveProofResult

__all__ = [
    "REQUIRED_STAGES",
    "CostGuard",
    "CostGuardExceeded",
    "CostReport",
    "LiveProofResult",
    "EvidenceBundle",
    "EvidenceIntegrityError",
    "ProviderCall",
    "ProviderCallLedger",
    "ProviderNotConfigured",
    "TurnMeasurement",
    "assert_all_real",
    "missing_provider_settings",
    "percentile",
    "require_providers",
    "run_pstn_probe",
    "runtime_info",
    "summarise",
    "write_evidence",
]


def __getattr__(name: str) -> Any:
    """Expose the probe's result type without importing the carrier stack.

    ``pstn_probe`` pulls in the ORM and the carrier SDK, so it is imported on
    first use: ``from app.telephony.live_proof import LiveProofResult`` works,
    while importing the package stays cheap.
    """
    if name in {"LiveProofResult", "ProviderNotConfigured"}:
        from app.telephony.live_proof import pstn_probe

        return getattr(pstn_probe, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def missing_provider_settings() -> tuple[str, ...]:
    """Provider settings a live run cannot start without."""
    from app.telephony.live_proof.pstn_probe import missing_provider_settings as _missing

    return _missing()


def require_providers() -> None:
    """Raise ``ProviderNotConfigured`` when a live run cannot be honest."""
    from app.telephony.live_proof.pstn_probe import require_providers as _require

    _require()


async def run_pstn_probe(**kwargs):
    """Place a real PSTN call and return what the live pipeline measured."""
    from app.telephony.live_proof.pstn_probe import run_pstn_probe as _run

    return await _run(**kwargs)
