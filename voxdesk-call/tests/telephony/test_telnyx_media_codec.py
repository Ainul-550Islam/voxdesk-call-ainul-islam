"""G.711 codec and the 8 kHz <-> 16 kHz resampler used by the Telnyx media stream.

The decode tables are the ITU-T G.711 reference tables, so the tests below pin
exact sample values. The encoder is a nearest-representable-value quantiser: it is
*not* bit-identical to ``audioop.lin2ulaw`` (which resolves midpoint ties the other
way), and these tests state that contract directly instead of asserting parity with
a module Python is removing.
"""

from __future__ import annotations

import pytest

from app.telephony.providers.telnyx_media import (
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    SUPPORTED_ENCODINGS,
    TelnyxMediaStreamError,
    alaw_to_pcm16,
    decode_g711,
    decode_to_pipeline_pcm,
    encode_from_pipeline_pcm,
    encode_g711,
    frame_bytes,
    frame_seconds,
    pcm16_frame_bytes,
    pcm16_to_alaw,
    pcm16_to_ulaw,
    resample_pcm16,
    ulaw_to_pcm16,
)

pytestmark = pytest.mark.unit


def _pcm(samples: list[int]) -> bytes:
    return b"".join(int(value).to_bytes(2, "little", signed=True) for value in samples)


def _samples(pcm: bytes) -> list[int]:
    return [
        int.from_bytes(pcm[index : index + 2], "little", signed=True)
        for index in range(0, len(pcm), 2)
    ]


# --------------------------------------------------------------------------- #
# G.711 decode vectors (ITU-T G.711 reference values)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0x00, -32124),
        (0x01, -31100),
        (0x02, -30076),
        (0x7F, 0),
        (0x80, 32124),
        (0xFF, 0),
    ],
)
def test_ulaw_decode_matches_the_reference_table(code: int, expected: int):
    assert _samples(ulaw_to_pcm16(bytes([code])))[0] == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0x00, -5504),
        (0x2A, -32256),
        (0x55, -8),
        (0x80, 5504),
        (0xAA, 32256),
        (0xD5, 8),
        (0xFF, 848),
    ],
)
def test_alaw_decode_matches_the_reference_table(code: int, expected: int):
    assert _samples(alaw_to_pcm16(bytes([code])))[0] == expected


def test_every_code_decodes_for_both_encodings():
    for code in range(256):
        assert len(ulaw_to_pcm16(bytes([code]))) == 2
        assert len(alaw_to_pcm16(bytes([code]))) == 2


# --------------------------------------------------------------------------- #
# G.711 encode: nearest representable value, bounded error
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pcm", "expected"),
    [
        (_pcm([0]), 0xFF),          # µ-law zero
        (_pcm([32767]), 0x80),      # positive full scale
        (_pcm([-32768]), 0x00),     # negative full scale
    ],
)
def test_ulaw_encode_hits_the_reference_extremes(pcm: bytes, expected: int):
    assert pcm16_to_ulaw(pcm) == bytes([expected])


@pytest.mark.parametrize(
    ("pcm", "expected"),
    [
        (_pcm([0]), 0xD5),          # +8 is the nearer of the two A-law zeros
        (_pcm([32767]), 0xAA),
        (_pcm([-32768]), 0x2A),
    ],
)
def test_alaw_encode_hits_the_reference_extremes(pcm: bytes, expected: int):
    assert pcm16_to_alaw(pcm) == bytes([expected])


def test_round_trip_error_stays_inside_the_quantiser_step():
    """The documented contract: decode(encode(x)) is the nearest code to x.

    Worst case is therefore one half-step of whichever segment x lands in. The
    observed maxima are 644 (µ-law, segment 0) and 512 (A-law), which is exactly
    the half-step of a 16-bit sample in the coarsest segment.
    """
    worst_ulaw = 0
    worst_alaw = 0
    for value in range(-32768, 32768, 7):
        original = _pcm([value])
        worst_ulaw = max(worst_ulaw, abs(_samples(ulaw_to_pcm16(pcm16_to_ulaw(original)))[0] - value))
        worst_alaw = max(worst_alaw, abs(_samples(alaw_to_pcm16(pcm16_to_alaw(original)))[0] - value))
    assert worst_ulaw <= 644
    assert worst_alaw <= 512


def test_encoded_code_is_stable_when_re_encoded():
    """Decode-then-encode is the identity for A-law and for all but one µ-law code.

    µ-law has two codes for zero (``0x7F`` and ``0xFF``). The quantiser can only
    return one of them, so ``0x7F`` round-trips to ``0xFF``. Every other code in
    both laws is stable, which is what matters for audio that has already been
    decoded once.
    """
    unstable = [
        code for code in range(256) if pcm16_to_ulaw(ulaw_to_pcm16(bytes([code]))) != bytes([code])
    ]
    assert unstable == [0x7F]
    assert pcm16_to_ulaw(ulaw_to_pcm16(b"\x7f")) == b"\xff"
    assert all(pcm16_to_alaw(alaw_to_pcm16(bytes([code]))) == bytes([code]) for code in range(256))


def test_encode_is_length_preserving_for_whole_frames():
    payload = _pcm([((index * 37) % 2000) - 1000 for index in range(160)])
    assert len(pcm16_to_ulaw(payload)) == 160
    assert len(pcm16_to_alaw(payload)) == 160


def test_odd_length_pcm_is_rejected_rather_than_silently_truncated():
    with pytest.raises(TelnyxMediaStreamError):
        pcm16_to_ulaw(b"\x00")


def test_decode_g711_rejects_unknown_encoding_and_odd_l16():
    with pytest.raises(TelnyxMediaStreamError) as error:
        decode_g711(b"\x00", "OPUS")
    assert error.value.code == "unsupported_encoding"
    with pytest.raises(TelnyxMediaStreamError):
        decode_g711(b"\x00", "L16")


def test_l16_passes_through_both_directions():
    pcm = _pcm([1, -1, 512, -512])
    assert decode_g711(pcm, "L16") == pcm
    assert encode_g711(pcm, "L16") == pcm


def test_supported_encodings_are_the_ones_the_carrier_offers():
    assert SUPPORTED_ENCODINGS == {"PCMU", "PCMA", "L16"}


# --------------------------------------------------------------------------- #
# Resampling
# --------------------------------------------------------------------------- #


def test_upsample_8k_to_16k_doubles_the_sample_count():
    pcm = _pcm([0] * 160)
    assert len(_samples(resample_pcm16(pcm, 8000, 16000))) == 320


def test_downsample_16k_to_8k_halves_the_sample_count():
    pcm = _pcm([0] * 320)
    assert len(_samples(resample_pcm16(pcm, 16000, 8000))) == 160


def test_resample_is_the_identity_at_the_same_rate():
    pcm = _pcm([100, -100, 200, -200])
    assert resample_pcm16(pcm, 16000, 16000) == pcm


def test_resample_preserves_a_constant_signal():
    """"A DC level must survive interpolation in both directions."""
    pcm = _pcm([1000] * 160)
    assert set(_samples(resample_pcm16(pcm, 8000, 16000))) == {1000}
    assert set(_samples(resample_pcm16(pcm, 16000, 8000))) == {1000}


def test_downsample_averages_the_window_so_high_frequencies_cannot_alias():
    """Alternating ±30000 at 16 kHz is above the 8 kHz Nyquist limit.

    The box filter must collapse it to the mean instead of letting it fold back
    into the audible band as a 4 kHz tone.
    """
    alternating = _pcm([30000, -30000] * 160)
    downsampled = _samples(resample_pcm16(alternating, 16000, 8000))
    assert max(abs(value) for value in downsampled) < 1000


def test_resample_rejects_non_positive_rates():
    with pytest.raises(TelnyxMediaStreamError):
        resample_pcm16(_pcm([1, 2]), 0, 8000)


def test_pipeline_decode_and_encode_round_trip_lengths():
    payload = bytes(160)
    pipeline_pcm = decode_to_pipeline_pcm(payload, encoding="PCMU", sample_rate=8000, pipeline_rate=16000)
    assert len(_samples(pipeline_pcm)) == 320
    back = encode_from_pipeline_pcm(pipeline_pcm, encoding="PCMU", sample_rate=8000, pipeline_rate=16000)
    assert len(back) == 160


def test_frame_geometry_matches_20ms_at_8k():
    assert frame_seconds(frame_ms=DEFAULT_FRAME_MS) == pytest.approx(0.02)
    assert frame_bytes(sample_rate=DEFAULT_SAMPLE_RATE, frame_ms=20) == 160
    assert pcm16_frame_bytes(sample_rate=8000, frame_ms=20) == 320
    assert pcm16_frame_bytes(sample_rate=16000, frame_ms=20) == 640
