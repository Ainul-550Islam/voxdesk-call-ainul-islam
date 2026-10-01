# VoxDesk Native Audio Engine — C/C++ Low-Level Audio/Video Processing Libraries

**Size:** 5-15MB binary (release stripped, AVX2/NEON optimized)
**Lines:** 5K-10K+ lines C/C++ (production, no skip)
**Purpose:** Audio noise reduction, voice packet decoding, custom WebRTC media engine module

## Architecture

- `third_party/` — opus (Opus codec), speexdsp (AEC & resampling), rnnoise (RNN noise suppression), pybind11 (zero-copy C++->Python), googletest
- `include/` — Enterprise C/C++ Header Interface, C-FFI umbrella header (extern "C" ABI for Rust/Python), AudioFrame, PCMBuffer, structs, Noise Suppressor, Echo Canceller, Gain Control, Opus Codec, Jitter Buffer, WebRTC Stream Interceptor
- `src/dsp/` — DSP Subsystem, Hybrid RNNoise + SpeexDSP spectral subtraction, Delay-estimator AEC3, Dynamic RMS AGC, Polyphase FIR Resampler 48kHz <-> 16kHz, Voice Activity Detector Energy+Pitch zero-latency
- `src/codec/` — Codec & Packet Transcoding, Opus packets -> raw 16-bit/32-bit Float PCM, PCM -> Opus ultra-low-bitrate, PLC reconstruct lost frames, Memory corruption guard
- `src/webrtc/` — WebRTC Native Media Pipeline, Lock-free SIMD-accelerated multi-track mixer, Adaptive jitter smoothing, Direct interceptor RTP payload before decoding, Binaural/3D spatial audio
- `src/ffi/` — FFI ABI Interop, C-compatible wrapper explicit memory management, Auto-generated bindgen Rust integration, Pybind11 Python bindings
- `src/memory/` — Ultra-Low Latency Memory Management, Atomic Lock-Free Ring Buffer Zero GC SPSC, Fixed-size chunk allocator Zero Heap Allocations, AVX2/ARM NEON SIMD intrinsics PCM math
- `src/utils/` — Logging Metrics Native Profiling, Lockless binary/text logger microsecond tracing, Execution time tracker per DSP stage, C-ABI error mapping
- `tests/` — QA Suite, SNR improvements, PCM->Opus->PCM fidelity, High-concurrency ring buffer stress, Microsecond latency benchmarks

## Build

```bash
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release -DENABLE_AVX2=ON ..
make -j$(nproc)
./voxdesk_audio_cli --benchmark
ctest --output-on-failure
```

## ABI Compatibility

- C-FFI: `include/voxdesk_audio.h` — extern "C" ABI boundary for Rust/Python
- Rust: `src/ffi/rust_bindings.rs` — bindgen safe Rust integration
- Python: `src/ffi/python_module.cpp` — pybind11 zero-copy bindings

## Performance

- AVX2/NEON SIMD accelerated PCM array math
- Lock-free SPSC ring buffer zero GC overhead
- Fixed-size chunk allocator zero heap allocations during processing
- Microsecond tracing per DSP stage
- 5-15MB binary stripped
