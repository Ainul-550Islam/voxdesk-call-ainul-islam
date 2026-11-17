"""Keypad input collected from the *caller* (PART 12, P12-15).

This is deliberately not the legacy IVR collection in :mod:`app.telephony.dtmf` — that one matches
a digit against a static menu and routes a call. Here the caller is answering the agent in the
middle of a conversation ("type your date of birth, then press pound"), so the important qualities
are different: the digits belong to the *dialogue*, they must never reach the transcript in clear
text, and the collection has to end for a reason the agent can describe.

Three modes are supported, matching the specification:

``end_key``      collect until the caller presses the end key (``#`` by default)
``fixed_digits`` collect exactly ``fixed_digits`` digits and stop
``timeout``      collect until ``timeout_s`` of silence after at least one digit

Every mode also stops at ``max_digits``, which is a safety net rather than a feature: a sticky key
must not be able to fill a buffer forever.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

DTMF_MODES: tuple[str, ...] = ("end_key", "fixed_digits", "timeout")

#: Digits a keypad can actually produce. A-D are included because some carriers send them.
VALID_DIGITS = frozenset("0123456789*#ABCD")


def _clean_digit(digit: str) -> str:
    value = str(digit or "").strip().upper()
    if not value:
        raise ValueError("empty DTMF digit")
    if len(value) != 1 or value not in VALID_DIGITS:
        raise ValueError(f"invalid DTMF digit {digit!r}")
    return value


def mask_digits(digits: str, *, keep_last: int = 2) -> str:
    """Mask a digit string, keeping only the last ``keep_last`` characters.

    The masked value is what goes into transcripts, logs and audit rows. Keeping the last two lets
    a support agent confirm *which* number was entered without learning the whole thing.
    """
    text = str(digits or "")
    if not text:
        return ""
    keep = max(0, min(int(keep_last), len(text)))
    return "*" * (len(text) - keep) + text[len(text) - keep :]


@dataclass(frozen=True)
class DtmfPolicy:
    """How a collection ends, and what may leave it."""

    mode: str = "end_key"
    end_key: str = "#"
    fixed_digits: int = 4
    timeout_s: float = 5.0
    max_digits: int = 32
    keep_last: int = 2
    prompt: str = ""

    def __post_init__(self) -> None:
        if self.mode not in DTMF_MODES:
            raise ValueError(f"unknown dtmf mode {self.mode!r}; expected one of {list(DTMF_MODES)}")
        if not (1 <= self.fixed_digits <= self.max_digits):
            raise ValueError("fixed_digits must be between 1 and max_digits")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        end = _clean_digit(self.end_key) if self.end_key else ""
        if self.mode == "end_key" and end in {"", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}:
            raise ValueError("end_key must be one of *#ABCD when mode is end_key")
        if self.keep_last < 0:
            raise ValueError("keep_last cannot be negative")


@dataclass(frozen=True)
class DtmfResult:
    digits: str
    masked: str
    complete: bool
    reason: str
    duration_s: float = 0.0
    policy: DtmfPolicy = field(default_factory=DtmfPolicy)

    def to_event(self) -> dict[str, Any]:
        """The `dtmf_input` event handed to the agent — masked, never raw."""
        return {
            "type": "dtmf_input",
            "digits_masked": self.masked,
            "digit_count": len(self.digits),
            "complete": self.complete,
            "reason": self.reason,
            "mode": self.policy.mode,
        }


class UserDtmfCollector:
    """Stateful collector; feed digits, read a result.

    No I/O and no clock of its own: `feed(..., at=)` takes the timestamp, so a timeout test is
    deterministic instead of sleeping.
    """

    def __init__(
        self,
        policy: DtmfPolicy | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.policy = policy or DtmfPolicy()
        self._clock = clock or time.monotonic
        self._digits: list[str] = []
        self._started_at: float | None = None
        self._last_at: float | None = None
        self._finished: DtmfResult | None = None

    # -- state ------------------------------------------------------------
    @property
    def digits(self) -> str:
        return "".join(self._digits)

    @property
    def finished(self) -> bool:
        return self._finished is not None

    def _finish(self, reason: str, *, at: float) -> DtmfResult:
        started = self._started_at if self._started_at is not None else at
        result = DtmfResult(
            digits=self.digits,
            masked=mask_digits(self.digits, keep_last=self.policy.keep_last),
            complete=reason in {"end_key", "fixed_digits", "timeout"},
            reason=reason,
            duration_s=round(max(0.0, at - started), 3),
            policy=self.policy,
        )
        self._finished = result
        return result

    # -- input ------------------------------------------------------------
    def feed(self, digit: str, *, at: float | None = None) -> DtmfResult | None:
        """Add one digit. Returns a result when the collection just ended, else ``None``."""
        if self._finished is not None:
            return self._finished
        stamp = self._clock() if at is None else float(at)
        if self._started_at is None:
            self._started_at = stamp
        self._last_at = stamp

        pressed = _clean_digit(digit)
        if self.policy.mode == "end_key" and pressed == self.policy.end_key:
            return self._finish("end_key", at=stamp)

        self._digits.append(pressed)

        if self.policy.mode == "fixed_digits" and len(self._digits) >= self.policy.fixed_digits:
            return self._finish("fixed_digits", at=stamp)
        if len(self._digits) >= self.policy.max_digits:
            return self._finish("max_digits", at=stamp)
        return None

    def tick(self, *, at: float | None = None) -> DtmfResult | None:
        """Called on the VAD tick; ends a `timeout` collection once silence exceeds the policy."""
        if self._finished is not None or self.policy.mode != "timeout":
            return None
        if self._last_at is None:
            return None
        stamp = self._clock() if at is None else float(at)
        if stamp - self._last_at >= self.policy.timeout_s and self._digits:
            return self._finish("timeout", at=stamp)
        return None

    def result(self) -> DtmfResult:
        """Read the current result, finishing as incomplete if the caller hangs up first."""
        if self._finished is not None:
            return self._finished
        stamp = self._last_at if self._last_at is not None else self._clock()
        return self._finish("incomplete", at=stamp)


def collect_keypad_input(
    digits: str | list[str],
    *,
    policy: DtmfPolicy | None = None,
) -> DtmfResult:
    """Convenience wrapper for a complete digit string (tests, replays, dashboard previews)."""
    collector = UserDtmfCollector(policy)
    for index, digit in enumerate(digits):
        collector.feed(digit, at=float(index))
    return collector.result()
