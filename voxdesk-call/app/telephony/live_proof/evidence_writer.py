"""Evidence files for a live proof run -- and the honesty rule that guards them.

The point of an evidence file is that a stranger can check the claim. So:

* every provider touch is recorded in a :class:`ProviderCallLedger` entry that
  names the provider, the pipeline stage, and the *artifact* the fact came from
  (the Twilio API response, the persisted transcript row, the pipeline's
  ``llm_used`` column, the latency observer's samples);
* ``provider_fakes`` is **computed** from that ledger. There is no parameter, no
  keyword argument and no environment variable that can set it: it is false only
  when every recorded call was real *and* all four required stages (telephony,
  STT, LLM, TTS) have a real entry. A run where one stage is unproven is
  reported as ``provider_fakes: true``, because an unproven stage is not a real
  one;
* percentiles are computed here from the measurements handed in, never pasted
  from a document;
* the JSON file is hashed and the hash is written next to the run row, so the
  artifact cannot be edited after the fact.

If a caller tries to write evidence whose ledger contradicts the claim, the
writer refuses with :class:`EvidenceIntegrityError` rather than producing a file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

#: Stages that must each have a real provider call for ``provider_fakes=false``.
REQUIRED_STAGES: tuple[str, ...] = ("telephony", "stt", "llm", "tts")


class EvidenceIntegrityError(RuntimeError):
    """The evidence would misrepresent the run. Nothing is written."""

    code = "live_proof_evidence_integrity"
    status_code = 409

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}


@dataclass(frozen=True)
class ProviderCall:
    """One provider interaction, with the artifact that proves it happened."""

    provider: str
    stage: str
    operation: str
    real: bool
    status: str = "ok"
    basis: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["real"] = bool(self.real)
        return payload


class ProviderCallLedger:
    """Append-only record of every provider interaction in one run."""

    def __init__(self) -> None:
        self._calls: list[ProviderCall] = []

    def record(
        self,
        *,
        provider: str,
        stage: str,
        operation: str,
        real: bool,
        status: str = "ok",
        basis: str = "",
        detail: dict[str, Any] | None = None,
    ) -> ProviderCall:
        name = (provider or "").strip().lower()
        stage_name = (stage or "").strip().lower()
        if not name:
            raise EvidenceIntegrityError("a provider call must name its provider")
        if stage_name not in REQUIRED_STAGES and stage_name != "evidence":
            raise EvidenceIntegrityError(f"unknown pipeline stage {stage!r}")
        call = ProviderCall(
            provider=name,
            stage=stage_name,
            operation=(operation or "").strip(),
            real=bool(real),
            status=(status or "ok").strip(),
            basis=(basis or "").strip(),
            detail=dict(detail or {}),
        )
        self._calls.append(call)
        return call

    def __len__(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> tuple[ProviderCall, ...]:
        return tuple(self._calls)

    def real_stages(self) -> set[str]:
        return {call.stage for call in self._calls if call.real and call.status == "ok"}

    def missing_real_stages(self) -> tuple[str, ...]:
        proven = self.real_stages()
        return tuple(stage for stage in REQUIRED_STAGES if stage not in proven)

    def fake_calls(self) -> tuple[ProviderCall, ...]:
        return tuple(call for call in self._calls if not call.real)

    @property
    def provider_fakes(self) -> bool:
        """True unless every call was real and every required stage is proven."""
        return bool(self.fake_calls()) or bool(self.missing_real_stages())

    def providers(self) -> list[str]:
        return sorted({call.provider for call in self._calls})

    def as_list(self) -> list[dict]:
        return [call.as_dict() for call in self._calls]

    def honesty_note(self) -> str:
        if not self.provider_fakes:
            return (
                "every recorded provider call was real: "
                + ", ".join(sorted(self.real_stages()))
            )
        missing = self.missing_real_stages()
        fake = len(self.fake_calls())
        parts = []
        if fake:
            parts.append(f"{fake} recorded provider call(s) were not real")
        if missing:
            parts.append("unproven stage(s): " + ", ".join(missing))
        return "; ".join(parts)


@dataclass(frozen=True)
class TurnMeasurement:
    """One measured conversational turn, as persisted by the voice pipeline."""

    turn_index: int
    speaker: str
    latency_ms: float | None
    text_chars: int
    created_at: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceBundle:
    json_path: str
    csv_path: str
    sha256: str
    provider_fakes: bool
    payload: dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "json_path": self.json_path,
            "csv_path": self.csv_path,
            "sha256": self.sha256,
            "provider_fakes": self.provider_fakes,
        }


def percentile(values: Iterable[float], q: float) -> float | None:
    """Percentile with linear interpolation; ``None`` when there is no data."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (max(0.0, min(100.0, q)) / 100.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def summarise(latencies_ms: Iterable[float]) -> dict[str, float | int | None]:
    values = [float(value) for value in latencies_ms if value is not None]
    return {
        "samples": len(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": round(max(values), 3) if values else None,
    }


def runtime_info() -> dict[str, Any]:
    """Host/runtime facts an operator needs to interpret the numbers."""
    info: dict[str, Any] = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "executable": Path(sys.executable).name,
    }
    mem_total_mb = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                mem_total_mb = round(int(line.split()[1]) / 1024.0, 1)
                break
    except OSError:
        mem_total_mb = None
    info["mem_total_mb"] = mem_total_mb
    cpu_model = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    info["cpu_model"] = cpu_model
    return info


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _csv_rows(turns: list[TurnMeasurement]) -> list[list[str]]:
    rows = [["turn_index", "speaker", "latency_ms", "text_chars", "created_at"]]
    for turn in turns:
        rows.append(
            [
                str(turn.turn_index),
                turn.speaker,
                "" if turn.latency_ms is None else f"{float(turn.latency_ms):.3f}",
                str(turn.text_chars),
                turn.created_at,
            ]
        )
    return rows


def write_evidence(
    *,
    run_id: str,
    tenant_id: str,
    evidence_dir: str | Path,
    ledger: ProviderCallLedger,
    turns: list[TurnMeasurement],
    stage_metrics: dict[str, dict[str, Any]],
    recording_sha256: str = "",
    run_meta: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    clock: datetime | None = None,
) -> EvidenceBundle:
    """Write the JSON + CSV evidence pair and return their identity.

    ``stage_metrics`` maps a stage name (``stt``, ``llm``, ``tts``, ``e2e``) to
    the measurements that stage produced. Values are never invented here: a
    stage with no samples is written as ``samples: 0`` and null percentiles.
    """
    now = clock or datetime.now(timezone.utc)
    provider_fakes = ledger.provider_fakes
    if not ledger.calls:
        raise EvidenceIntegrityError(
            "refusing to write evidence with an empty provider ledger: a run with "
            "no recorded provider call cannot claim anything"
        )

    directory = Path(evidence_dir) / now.date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "schema": "voxdesk.live_proof.evidence.v1",
        "run_id": run_id,
        "tenant_id": tenant_id,
        "generated_at": now.isoformat(),
        "provider_fakes": provider_fakes,
        "provider_honesty_note": ledger.honesty_note(),
        "required_stages": list(REQUIRED_STAGES),
        "missing_real_stages": list(ledger.missing_real_stages()),
        "providers": ledger.providers(),
        "provider_calls": ledger.as_list(),
        "stage_metrics": stage_metrics,
        "turns": [turn.as_dict() for turn in turns],
        "recording_sha256": recording_sha256,
        "runtime": runtime_info(),
        "run": dict(run_meta or {}),
        "notes": list(notes or []),
    }

    json_path = directory / f"{run_id}.json"
    csv_path = directory / f"{run_id}.csv"

    # The digest is over the file as written and lives in the run row, not inside
    # the file: a hash cannot contain itself, and a reader must be able to
    # recompute it from the bytes on disk.
    encoded = (json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n").encode("utf-8")
    json_path.write_bytes(encoded)

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(_csv_rows(turns))

    return EvidenceBundle(
        json_path=str(json_path),
        csv_path=str(csv_path),
        sha256=_sha256_bytes(json_path.read_bytes()),
        provider_fakes=provider_fakes,
        payload=payload,
    )


def assert_all_real(ledger: ProviderCallLedger, *, context: str = "live proof run") -> None:
    """Raise when the ledger cannot support a "no fakes" claim."""
    if ledger.provider_fakes:
        raise EvidenceIntegrityError(f"{context} cannot claim real providers: {ledger.honesty_note()}")
