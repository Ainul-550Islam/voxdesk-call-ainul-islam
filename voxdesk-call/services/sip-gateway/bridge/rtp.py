"""RTP framing for the bridge (PART 13, P13-01).

The gateway-facing parser intentionally supports RFC 3550's fixed header,
CSRC list, header extension, and padding. SRTP is terminated by the network
edge; this code only accepts the resulting RTP datagrams.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

VERSION = 2
HEADER_SIZE = 12
MAX_PACKET_SIZE = 65_535
#: Payload types that mean "this is RFC 2833 telephone-event, not audio".
TELEPHONE_EVENT_TYPES = (101, 100)


@dataclass(frozen=True)
class RtpPacket:
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes
    marker: bool = False

    @property
    def is_telephone_event(self) -> bool:
        return self.payload_type in TELEPHONE_EVENT_TYPES

    def as_dict(self) -> dict:
        return {
            "payload_type": self.payload_type,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "ssrc": self.ssrc,
            "bytes": len(self.payload),
            "telephone_event": self.is_telephone_event,
        }


def parse_packet(data: bytes) -> RtpPacket:
    """Parse an RTP datagram and reject every truncated header construct."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("RTP packet must be bytes-like")
    if len(data) < HEADER_SIZE:
        raise ValueError("RTP packet is shorter than its header")
    if len(data) > MAX_PACKET_SIZE:
        raise ValueError("RTP packet exceeds the maximum UDP payload size")

    first, second, sequence, timestamp, ssrc = struct.unpack(">BBHII", data[:HEADER_SIZE])
    version = first >> 6
    if version != VERSION:
        raise ValueError(f"RTP version {version} is not {VERSION}")

    padding = bool(first & 0x20)
    extension = bool(first & 0x10)
    csrc_count = first & 0x0F
    offset = HEADER_SIZE + csrc_count * 4
    if len(data) < offset:
        raise ValueError("RTP CSRC list is truncated")

    if extension:
        if len(data) < offset + 4:
            raise ValueError("RTP header claims an extension that is not there")
        extension_words = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        extension_end = offset + 4 + extension_words * 4
        if extension_end > len(data):
            raise ValueError("RTP header extension is truncated")
        offset = extension_end

    payload = bytes(data[offset:])
    if padding:
        if not payload:
            raise ValueError("RTP padding flag is set without padding bytes")
        pad = payload[-1]
        if pad == 0:
            raise ValueError("RTP padding length must be nonzero")
        if pad > len(payload):
            raise ValueError("RTP padding length exceeds the payload")
        payload = payload[:-pad]

    return RtpPacket(
        payload_type=second & 0x7F,
        marker=bool(second & 0x80),
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        payload=payload,
    )


def build_packet(
    payload: bytes,
    *,
    payload_type: int,
    sequence: int,
    timestamp: int,
    ssrc: int,
    marker: bool = False,
) -> bytes:
    """Build an RTP v2 packet, applying only the protocol's modular fields."""
    for name, value, maximum in (
        ("payload_type", payload_type, 127),
        ("sequence", sequence, 0xFFFF),
        ("timestamp", timestamp, 0xFFFFFFFF),
        ("ssrc", ssrc, 0xFFFFFFFF),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
            raise ValueError(f"RTP {name} must be an integer from 0 to {maximum}")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("RTP payload must be bytes-like")
    if len(payload) + HEADER_SIZE > MAX_PACKET_SIZE:
        raise ValueError("RTP packet exceeds the maximum UDP payload size")
    second = (0x80 if marker else 0) | payload_type
    header = struct.pack(">BBHII", VERSION << 6, second, sequence, timestamp, ssrc)
    return header + bytes(payload)


def sequence_gap(previous: int, current: int) -> int:
    """Forward distance between two 16-bit sequence numbers (0 when in order)."""
    return (current - previous) & 0xFFFF
