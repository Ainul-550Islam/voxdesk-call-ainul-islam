"""Hard spend cap for a live PSTN proof run.

A live probe dials a real number and drives real STT, LLM and TTS providers, so
it costs money the moment it starts. This guard is the abort switch:

* the run is refused before the call is placed when the configured cap is
  already exhausted or the caps are not positive;
* the elapsed time is checked on every poll tick, and when either cap is
  crossed the guard raises :class:`CostGuardExceeded`, which the probe converts
  into "hang the call up now, record the abort, write no success evidence";
* the spend figure is derived from *elapsed minutes* and an operator-configured
  rate. It is a budget estimate, not a provider invoice, and every evidence file
  that carries it says so.

Nothing here guesses: a missing rate is a configuration error, not a zero.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from app.core.config import settings


class CostGuardExceeded(RuntimeError):
    """The run crossed its hard minutes or spend cap and must stop now."""

    code = "live_proof_cost_cap"

    def __init__(self, message: str, *, minutes: float, usd: float, cap: str) -> None:
        super().__init__(message)
        self.message = message
        self.minutes = minutes
        self.usd = usd
        self.cap = cap

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "minutes": round(self.minutes, 4),
            "usd": round(self.usd, 6),
            "cap": self.cap,
        }


@dataclass(frozen=True)
class CostReport:
    """One measurement of elapsed time and estimated spend."""

    minutes: float
    usd: float
    max_minutes: float
    max_usd: float

    @property
    def within_caps(self) -> bool:
        return self.minutes <= self.max_minutes and self.usd <= self.max_usd

    @property
    def remaining_minutes(self) -> float:
        return max(0.0, self.max_minutes - self.minutes)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.max_usd - self.usd)

    def as_dict(self) -> dict:
        return {
            "minutes": round(self.minutes, 4),
            "usd": round(self.usd, 6),
            "max_minutes": self.max_minutes,
            "max_usd": self.max_usd,
            "within_caps": self.within_caps,
            "remaining_minutes": round(self.remaining_minutes, 4),
            "remaining_usd": round(self.remaining_usd, 6),
        }


class CostGuard:
    """A per-run minutes/spend ceiling with a monotonic clock."""

    def __init__(
        self,
        *,
        max_minutes: float,
        max_usd: float,
        usd_per_minute: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_minutes <= 0:
            raise ValueError("max_minutes must be positive")
        if max_usd <= 0:
            raise ValueError("max_usd must be positive")
        if usd_per_minute < 0:
            raise ValueError("usd_per_minute must not be negative")
        self.max_minutes = float(max_minutes)
        self.max_usd = float(max_usd)
        self.usd_per_minute = float(usd_per_minute)
        self._clock = clock or time.monotonic
        self._started_at: float | None = None
        self._stopped_at: float | None = None

    @classmethod
    def from_settings(cls, *, clock: Callable[[], float] | None = None) -> "CostGuard":
        return cls(
            max_minutes=float(settings.live_proof_max_minutes),
            max_usd=float(settings.live_proof_max_usd),
            usd_per_minute=float(settings.live_proof_usd_per_minute),
            clock=clock,
        )

    # ------------------------------------------------------------- lifecycle --

    def start(self) -> CostReport:
        self._started_at = self._clock()
        self._stopped_at = None
        return self.report()

    def stop(self) -> CostReport:
        if self._started_at is not None and self._stopped_at is None:
            self._stopped_at = self._clock()
        return self.report()

    @property
    def started(self) -> bool:
        return self._started_at is not None

    # ------------------------------------------------------------ measurement --

    def elapsed_minutes(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._stopped_at if self._stopped_at is not None else self._clock()
        return max(0.0, (end - self._started_at) / 60.0)

    def spend_usd(self) -> float:
        return self.elapsed_minutes() * self.usd_per_minute

    def report(self) -> CostReport:
        return CostReport(
            minutes=self.elapsed_minutes(),
            usd=self.spend_usd(),
            max_minutes=self.max_minutes,
            max_usd=self.max_usd,
        )

    def ensure_within_cap(self) -> CostReport:
        """Raise :class:`CostGuardExceeded` the moment a cap is crossed."""
        report = self.report()
        if report.minutes > self.max_minutes:
            raise CostGuardExceeded(
                f"live proof exceeded LIVE_PROOF_MAX_MINUTES ({self.max_minutes} min)",
                minutes=report.minutes,
                usd=report.usd,
                cap="minutes",
            )
        if report.usd > self.max_usd:
            raise CostGuardExceeded(
                f"live proof exceeded LIVE_PROOF_MAX_USD (${self.max_usd:.2f})",
                minutes=report.minutes,
                usd=report.usd,
                cap="usd",
            )
        return report

    def tick(self) -> CostReport:
        """Alias used by the probe's poll loop."""
        return self.ensure_within_cap()
