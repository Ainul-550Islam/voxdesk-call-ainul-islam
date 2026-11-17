"""Rebuild `assets/hold/loop-default.ogg` from scratch (PART 13, P13-09).

The hold-music loop is a repository asset with a CC0 dedication, so it has to be
reproducible: an operator who doubts its provenance can regenerate it and
compare. This script is that proof -- four sine-partial chords, a slow tremolo,
a 0.5 s seam crossfade, encoded to Ogg Vorbis.

    python3 scripts/make_hold_music.py            # writes assets/hold/loop-default.ogg
    python3 scripts/make_hold_music.py --check    # verifies the file is Ogg Vorbis

Requires numpy and an ffmpeg binary; `imageio-ffmpeg` supplies one for CI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "hold" / "loop-default.ogg"
SAMPLE_RATE = 44_100
DURATION = 30.0
CHORDS = (
    (220.00, 261.63, 329.63, 392.00),  # Am7
    (174.61, 220.00, 261.63, 329.63),  # Fmaj7
    (130.81, 196.00, 261.63, 329.63),  # Cmaj7
    (196.00, 246.94, 293.66, 329.63),  # G6
)


def synth() -> bytes:
    import numpy as np

    total = int(SAMPLE_RATE * DURATION)
    timeline = np.linspace(0, DURATION, total, endpoint=False)
    signal = np.zeros(total)
    segment = DURATION / len(CHORDS)
    for index, chord in enumerate(CHORDS):
        start = int(index * segment * SAMPLE_RATE)
        end = int((index + 1) * segment * SAMPLE_RATE)
        length = end - start
        if length <= 0:
            continue
        block = np.zeros(length)
        for voice, frequency in enumerate(chord):
            for detune, amplitude in ((0.0, 0.16), (0.7, 0.10)):
                phase = 2 * np.pi * (frequency + detune) * timeline[start:end]
                block += (amplitude / (voice + 1)) * np.sin(phase)
        block *= 0.9 + 0.1 * np.sin(2 * np.pi * 0.25 * timeline[start:end])
        fade = min(int(0.35 * SAMPLE_RATE), length // 2)
        envelope = np.ones(length)
        if fade:
            envelope[:fade] = np.linspace(0, 1, fade)
            envelope[-fade:] = np.linspace(1, 0, fade)
        signal[start:end] += block * envelope

    signal = signal / np.max(np.abs(signal)) * 0.6
    seam = int(0.5 * SAMPLE_RATE)
    signal[:seam] *= np.linspace(0, 1, seam)
    signal[-seam:] *= np.linspace(1, 0, seam)
    return (signal * 32767).astype("<i2").tobytes()


def write_pcm(payload: bytes) -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    with wave.open(handle.name, "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(payload)
    return handle.name


def ffmpeg_binary() -> str:
    try:
        import imageio_ffmpeg
    except ModuleNotFoundError:
        return "ffmpeg"
    return imageio_ffmpeg.get_ffmpeg_exe()


def build() -> int:
    pcm_path = write_pcm(synth())
    try:
        subprocess.run(
            [
                ffmpeg_binary(), "-y", "-loglevel", "error", "-i", pcm_path,
                "-c:a", "libvorbis", "-q:a", "4", str(TARGET),
            ],
            check=True,
        )
    finally:
        os.unlink(pcm_path)
    print(f"wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size} bytes)")
    return 0


def check() -> int:
    if not TARGET.is_file():
        print(f"missing {TARGET.relative_to(ROOT)}", file=sys.stderr)
        return 1
    header = TARGET.read_bytes()[:64]
    if not header.startswith(b"OggS"):
        print("the hold-music asset is not an Ogg stream", file=sys.stderr)
        return 1
    if b"vorbis" not in TARGET.read_bytes():
        print("the hold-music asset has no Vorbis codec header", file=sys.stderr)
        return 1
    print(f"{TARGET.relative_to(ROOT)}: Ogg Vorbis, {TARGET.stat().st_size} bytes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify instead of rebuild")
    args = parser.parse_args()
    return check() if args.check else build()


if __name__ == "__main__":
    raise SystemExit(main())
