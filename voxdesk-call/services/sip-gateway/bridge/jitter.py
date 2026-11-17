"""Bounded, 16-bit-wrap-safe RTP jitter buffer (PART 13, P13-01).

The buffer keeps a small sequence-number window, emits packets in RTP order,
counts packets skipped as loss, and rejects packets that arrive after playback
has advanced. Sequence numbers are compared modulo 2^16, so rollover from
65535 to 0 is ordinary forward progress rather than a giant late packet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_DEPTH = 2
MAX_DEPTH = 20
INITIAL_DEPTH = 4
_SEQUENCE_MODULUS = 1 << 16
_SEQUENCE_HALF_RANGE = 1 << 15


@dataclass
class JitterBuffer:
    depth: int = INITIAL_DEPTH
    lost: int = 0
    late: int = 0
    received: int = 0
    duplicates: int = 0
    overflow: int = 0
    last_sequence: int | None = None
    _packets: dict[int, bytes] = field(default_factory=dict)
    _expected: int | None = None

    def __post_init__(self) -> None:
        if not MIN_DEPTH <= self.depth <= MAX_DEPTH:
            raise ValueError(f"jitter depth must be between {MIN_DEPTH} and {MAX_DEPTH}")

    @staticmethod
    def _forward_distance(start: int, target: int) -> int:
        return (target - start) & 0xFFFF

    def _ordered_sequences(self) -> list[int]:
        if self._expected is None:
            return list(self._packets)
        return sorted(
            self._packets,
            key=lambda sequence: self._forward_distance(self._expected, sequence),
        )

    def push(self, sequence: int, payload: bytes) -> bool:
        """Offer a packet; return false for late, duplicate, or overflow drops."""
        if not isinstance(sequence, int) or isinstance(sequence, bool) or not 0 <= sequence < _SEQUENCE_MODULUS:
            raise ValueError("RTP sequence must be an integer from 0 to 65535")
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("jitter payload must be bytes-like")
        self.received += 1

        if self._expected is None:
            self._expected = sequence
        distance = self._forward_distance(self._expected, sequence)
        if distance >= _SEQUENCE_HALF_RANGE:
            self.late += 1
            return False
        if sequence in self._packets:
            self.duplicates += 1
            return False

        if len(self._packets) >= MAX_DEPTH:
            sequences = self._ordered_sequences()
            farthest = sequences[-1]
            farthest_distance = self._forward_distance(self._expected, farthest)
            if distance >= farthest_distance:
                self.overflow += 1
                return False
            del self._packets[farthest]
            self.overflow += 1

        self._packets[sequence] = bytes(payload)
        return True

    def pop(self) -> bytes | None:
        """Take the next in-order payload when the target occupancy is reached."""
        if not self._packets or len(self._packets) < self.depth:
            return None
        if self._expected is None:
            self._expected = self._ordered_sequences()[0]

        payload = self._packets.pop(self._expected, None)
        if payload is None:
            sequences = self._ordered_sequences()
            if not sequences:
                return None
            next_sequence = sequences[0]
            gap = self._forward_distance(self._expected, next_sequence)
            # _ordered_sequences sorts relative to expected, so only a valid
            # forward gap can occur here. Never jump more than half a sequence
            # space on corrupt or stale input.
            if not 0 < gap < _SEQUENCE_HALF_RANGE:
                self.late += 1
                return None
            self.lost += gap
            self._expected = next_sequence
            payload = self._packets.pop(next_sequence)
            self.widen()
        else:
            next_sequence = self._expected
            self.narrow()

        self.last_sequence = next_sequence
        self._expected = (next_sequence + 1) & 0xFFFF
        return payload

    def widen(self) -> int:
        self.depth = min(MAX_DEPTH, self.depth + 1)
        return self.depth

    def narrow(self) -> int:
        self.depth = max(MIN_DEPTH, self.depth - 1)
        return self.depth

    def flush(self) -> list[bytes]:
        """Return buffered media in sequence order and reset the sequence anchor."""
        ordered = [self._packets[sequence] for sequence in self._ordered_sequences()]
        self._packets.clear()
        self._expected = None
        self.last_sequence = None
        return ordered

    @property
    def buffered(self) -> int:
        return len(self._packets)

    def stats(self) -> dict:
        total = self.received + self.lost
        return {
            "depth": self.depth,
            "buffered": self.buffered,
            "received": self.received,
            "lost": self.lost,
            "late": self.late,
            "duplicates": self.duplicates,
            "overflow": self.overflow,
            "loss_ratio": round(self.lost / total, 4) if total else 0.0,
        }
