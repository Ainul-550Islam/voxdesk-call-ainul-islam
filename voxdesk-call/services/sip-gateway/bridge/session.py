"""RTP <-> WebSocket media bridge (PART 13, P13-01).

The SIP gateway's media protocol carries the negotiated G.711 payloads as
binary WebSocket frames. The app's standalone serializer decodes those frames
to pipeline PCM exactly once. ``ingest``/``egress`` remain PCM16 helpers for
offline DSP tests and callers that explicitly request PCM.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Iterable

from . import codecs, dtmf, jitter, rtp

FRAME_AUDIO = "audio"
FRAME_DTMF = "dtmf"
FRAME_END = "end"


@dataclass(frozen=True)
class BridgeFrame:
    kind: str
    payload: bytes
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "bytes": len(self.payload), "meta": dict(self.meta)}


@dataclass
class BridgeSession:
    """One call's codec, sequence, jitter and DTMF state."""

    call_id: str
    codec: str = "PCMU"
    telephone_event_payload_type: int = 101
    buffer: jitter.JitterBuffer = field(default_factory=jitter.JitterBuffer)
    decoder: dtmf.DtmfDecoder = field(default_factory=dtmf.DtmfDecoder)
    local_ssrc: int = field(default_factory=lambda: secrets.randbits(32) or 1)
    frames_out: int = 0
    packets_in: int = 0
    dtmf_events: int = 0

    def __post_init__(self) -> None:
        codecs.codec_for(self.codec)
        if not 0 <= self.telephone_event_payload_type <= 127:
            raise ValueError("telephone-event payload type must be from 0 to 127")
        if self.telephone_event_payload_type == codecs.codec_for(self.codec)[1]:
            raise ValueError("telephone-event and audio must use different payload types")
        if not 0 <= self.local_ssrc <= 0xFFFFFFFF:
            raise ValueError("RTP SSRC must be from 0 to 4294967295")

    def _drain_audio(self, *, source_sequence: int, pcm16: bool) -> list[BridgeFrame]:
        frames: list[BridgeFrame] = []
        while True:
            payload = self.buffer.pop()
            if payload is None:
                break
            # Telephone-event packets occupy the same RTP sequence space but
            # are represented by an empty jitter payload, not audio.
            if not payload:
                continue
            audio = codecs.to_pcm16(payload, self.codec) if pcm16 else payload
            frames.append(
                BridgeFrame(
                    FRAME_AUDIO,
                    audio,
                    {
                        "sequence": self.buffer.last_sequence
                        if self.buffer.last_sequence is not None
                        else source_sequence,
                        "codec": self.codec,
                    },
                )
            )
        self.frames_out += len(frames)
        return frames

    def _ingest(self, packet: bytes, *, pcm16: bool) -> list[BridgeFrame]:
        parsed = rtp.parse_packet(packet)
        self.packets_in += 1
        expected_audio_type = codecs.codec_for(self.codec)[1]
        is_telephone_event = parsed.payload_type == self.telephone_event_payload_type

        if is_telephone_event:
            # RTP audio and telephone-event packets share one sequence number
            # space. Insert a no-audio placeholder so DTMF does not look like
            # packet loss to the jitter buffer.
            self.buffer.push(parsed.sequence, b"")
            frames = self._drain_audio(source_sequence=parsed.sequence, pcm16=pcm16)
            key = self.decoder.feed(parsed.payload, timestamp=parsed.timestamp)
            if key is not None:
                if key.started:
                    self.dtmf_events += 1
                frames.append(
                    BridgeFrame(
                        FRAME_DTMF,
                        key.digit.encode("ascii"),
                        {
                            "digit": key.digit,
                            "duration": key.duration,
                            "phase": "start" if key.started else "end",
                            "event": key.event,
                        },
                    )
                )
            return frames

        if parsed.payload_type != expected_audio_type:
            raise ValueError(
                f"unexpected RTP payload type {parsed.payload_type}; "
                f"{self.codec} requires {expected_audio_type}"
            )
        self.buffer.push(parsed.sequence, parsed.payload)
        return self._drain_audio(source_sequence=parsed.sequence, pcm16=pcm16)

    def ingest(self, packet: bytes) -> list[BridgeFrame]:
        """Take RTP and return 16-kHz PCM/DTMF frames for DSP tests."""
        return self._ingest(packet, pcm16=True)

    def ingest_g711(self, packet: bytes) -> list[BridgeFrame]:
        """Take RTP and return raw G.711/DTMF frames for the app WebSocket."""
        return self._ingest(packet, pcm16=False)

    def egress(self, pcm: bytes, *, sequence: int, timestamp: int) -> bytes:
        """Convert PCM16 from the app to negotiated G.711 and wrap it in RTP."""
        payload = codecs.from_pcm16(pcm, self.codec)
        return self.egress_g711(payload, sequence=sequence, timestamp=timestamp)

    def egress_g711(self, payload: bytes, *, sequence: int, timestamp: int) -> bytes:
        """Frame an already encoded G.711 payload without transcoding it."""
        if not payload:
            return b""
        if len(payload) > 1400:
            raise ValueError("G.711 RTP payload exceeds the safe datagram size")
        return rtp.build_packet(
            payload,
            payload_type=codecs.codec_for(self.codec)[1],
            sequence=sequence,
            timestamp=timestamp,
            ssrc=self.local_ssrc,
        )

    def stats(self) -> dict:
        return {
            "call_id": self.call_id,
            "codec": self.codec,
            "packets_in": self.packets_in,
            "frames_out": self.frames_out,
            "dtmf_events": self.dtmf_events,
            "jitter": self.buffer.stats(),
        }


def split_frames(frames: Iterable[BridgeFrame]) -> tuple[list[bytes], list[dict]]:
    """Separate a frame stream into audio payloads and DTMF events."""
    audio: list[bytes] = []
    events: list[dict] = []
    for frame in frames:
        if frame.kind == FRAME_AUDIO:
            audio.append(frame.payload)
        elif frame.kind == FRAME_DTMF:
            events.append({"type": "dtmf", **frame.meta})
    return audio, events
