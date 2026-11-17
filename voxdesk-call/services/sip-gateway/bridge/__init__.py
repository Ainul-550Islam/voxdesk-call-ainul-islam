"""RTP <-> WebSocket PCM bridge (PART 13, P13-01).

Modules, in dependency order:

* `codecs`  -- G.711 mu/A-law tables and the 8 kHz <-> 16 kHz conversion;
* `rtp`     -- RFC 3550 parse/build, no crypto (rtpengine terminates SRTP);
* `jitter`  -- an adaptive jitter buffer that drops late audio;
* `dtmf`    -- RFC 2833 keypress decoding into one event per press;
* `session` -- the per-call object that ties the four together.

`main.py` runs it as a process against rtpengine; the tests import the four
modules directly, which is why nothing here touches a socket at import time.
"""

from .codecs import CODECS, FRAME_SAMPLES, RTP_CLOCK_RATE, TARGET_RATE, codec_for
from .dtmf import DtmfDecoder, DtmfKey
from .jitter import JitterBuffer
from .rtp import RtpPacket, build_packet, parse_packet
from .session import BridgeFrame, BridgeSession, split_frames

__all__ = [
    "BridgeFrame",
    "BridgeSession",
    "CODECS",
    "DtmfDecoder",
    "DtmfKey",
    "FRAME_SAMPLES",
    "JitterBuffer",
    "RTP_CLOCK_RATE",
    "RtpPacket",
    "TARGET_RATE",
    "build_packet",
    "codec_for",
    "parse_packet",
    "split_frames",
]
