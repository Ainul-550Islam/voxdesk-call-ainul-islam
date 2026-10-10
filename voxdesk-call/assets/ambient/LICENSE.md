# Ambient Audio Assets License (`assets/ambient/`)

All WAV files in `assets/ambient/` (`office.wav`, `call_center.wav`, `coffee_shop.wav`, `convention_hall.wav`, `summer_outdoor.wav`) are procedurally synthesized 8 kHz 16-bit mono PCM audio loops generated specifically for the VoxDesk Voice Runtime (`app/agent/audio/ambient.py`).

- **License**: CC0 1.0 Universal (Public Domain Dedication)
- **Format**: RIFF WAVE, 16-bit signed little-endian PCM (`PCM_16`), 8000 Hz, 1 channel (mono)
- **Looping**: Seamless loop boundary for `pipecat.audio.mixers.soundfile_mixer.SoundfileMixer`
