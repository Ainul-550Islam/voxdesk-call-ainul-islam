"""RFC 2833/4733 telephone-event to bounded DTMF key events.

A held key is retransmitted as a series of RTP telephone-event packets; the
end bit may itself be retransmitted. The decoder emits one key start per
keypress, tracks one end event, and de-duplicates repeated terminal packets by
RTP timestamp so an IVR does not receive a digit several times.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

DIGITS = {
    0: "0",
    1: "1",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "*",
    11: "#",
    12: "A",
    13: "B",
    14: "C",
    15: "D",
}
#: Minimum duration for a press to count, in RTP timestamp units (8 kHz).
MIN_DURATION = 160
END_FLAG = 0x80
_RESERVED_MASK = 0x70
MAX_RETAINED_EVENTS = 512


@dataclass(frozen=True)
class DtmfKey:
    digit: str
    event: int
    duration: int
    started: bool
    ended: bool

    def as_dict(self) -> dict:
        return {
            "digit": self.digit,
            "event": self.event,
            "duration": self.duration,
            "started": self.started,
            "ended": self.ended,
        }


@dataclass
class DtmfDecoder:
    """Stateful decoder with a bounded diagnostic event history."""

    min_duration: int = MIN_DURATION
    _current: int | None = None
    _duration: int = 0
    _emitted: bool = False
    _last_ended: tuple[int, int] | None = None
    events: list[DtmfKey] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.min_duration < 0:
            raise ValueError("minimum DTMF duration cannot be negative")

    def _record(self, key: DtmfKey) -> DtmfKey:
        self.events.append(key)
        if len(self.events) > MAX_RETAINED_EVENTS:
            del self.events[: len(self.events) - MAX_RETAINED_EVENTS]
        return key

    def _finish(self, event: int, duration: int, *, timestamp: int | None) -> DtmfKey | None:
        self._current = None
        self._duration = 0
        was_emitted = self._emitted
        self._emitted = False
        if timestamp is not None:
            self._last_ended = (event, timestamp & 0xFFFFFFFF)
        if duration < self.min_duration:
            return None
        # The E bit can arrive on the first observed packet (or after a short
        # event crossed the duration threshold). A single key represents both
        # transitions in that case, preserving one digit in the history.
        if not was_emitted:
            return self._record(
                DtmfKey(DIGITS[event], event, duration, started=True, ended=True)
            )
        return self._record(DtmfKey(DIGITS[event], event, duration, started=False, ended=True))

    def feed(self, payload: bytes, *, timestamp: int | None = None) -> DtmfKey | None:
        """Decode one four-byte telephone-event payload.

        ``timestamp`` is the RTP header timestamp. Passing it enables exact
        de-duplication of retransmitted end-of-event packets while still
        allowing two consecutive presses of the same digit.
        """
        if len(payload) < 4:
            raise ValueError("a telephone-event payload is four bytes")
        event, flags, duration = struct.unpack(">BBH", payload[:4])
        if event not in DIGITS:
            return None
        if flags & _RESERVED_MASK:
            raise ValueError("telephone-event reserved flag bits must be zero")
        ended = bool(flags & END_FLAG)
        if ended and timestamp is not None:
            terminal = (event, timestamp & 0xFFFFFFFF)
            if terminal == self._last_ended:
                return None

        if self._current != event:
            # A different event implicitly ended the prior key. Its state must
            # not be mistaken for a start already emitted for this new digit.
            self._current = None
            self._duration = 0
            self._emitted = False
            if ended:
                return self._finish(event, duration, timestamp=timestamp)
            self._current = event
            self._duration = duration
            self._emitted = duration >= self.min_duration
            if self._emitted:
                return self._record(
                    DtmfKey(DIGITS[event], event, duration, started=True, ended=False)
                )
            return None

        self._duration = max(self._duration, duration)
        if ended:
            return self._finish(event, self._duration, timestamp=timestamp)
        if not self._emitted and self._duration >= self.min_duration:
            self._emitted = True
            return self._record(
                DtmfKey(DIGITS[event], event, self._duration, started=True, ended=False)
            )
        return None

    def digits(self) -> str:
        """The keypresses retained in the bounded event history."""
        return "".join(key.digit for key in self.events if key.started)

    def reset(self) -> None:
        self._current = None
        self._duration = 0
        self._emitted = False
        self._last_ended = None
        self.events.clear()
