# Cross-compilation build environment (Clang-15, GCC-12, AVX2 optimization) — 5-15MB binary
FROM ubuntu:22.04 as builder
RUN apt-get update && apt-get install -y clang-15 gcc-12 g++-12 cmake ninja-build pkg-config libopus-dev libspeexdsp-dev python3-dev pybind11-dev git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY CMakeLists.txt ./
COPY include/ include/
COPY src/ src/
COPY third_party/ third_party/
COPY tests/ tests/
RUN mkdir build && cd build && cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-15 -DCMAKE_CXX_COMPILER=clang++-15 -DENABLE_AVX2=ON -GNinja .. && ninja -j$(nproc) && strip voxdesk_audio_cli libvoxdesk_audio.so && ls -lh voxdesk_audio_cli libvoxdesk_audio.so

FROM ubuntu:22.04 as runtime
RUN apt-get update && apt-get install -y libopus0 libspeexdsp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/build/voxdesk_audio_cli /usr/local/bin/
COPY --from=builder /app/build/libvoxdesk_audio.so /usr/local/lib/
COPY --from=builder /app/include/ /usr/local/include/voxdesk/
ENV LD_LIBRARY_PATH=/usr/local/lib
CMD ["voxdesk_audio_cli", "--help"]

FROM scratch as minimal
COPY --from=builder /app/build/voxdesk_audio_cli /voxdesk_audio_cli
ENTRYPOINT ["/voxdesk_audio_cli"]
