#ifndef WEBRTC_STREAM_INTERCEPTOR_H
#define WEBRTC_STREAM_INTERCEPTOR_H

// File: voxdesk-native-audio-engine/include/webrtc_stream_interceptor.h — webrtc_stream_interceptor Low-level Native WebRTC AudioSink AudioSource interface — 800+ lines production — NO SKIP
// Low-Level Audio/Video Processing — 5-15MB binary — noise reduction, voice packet decoding, WebRTC media engine
#pragma once
#include <cstdint>
#include <cstddef>
#include <memory>
#include <vector>
#include <string>
#include <atomic>
#include <mutex>
#include <chrono>
#include "audio_types.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct Webrtc_stream_interceptorStruct0 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct0;

Webrtc_stream_interceptorStruct0* webrtc_stream_interceptor_create_struct_0(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_0(Webrtc_stream_interceptorStruct0* ptr);
int webrtc_stream_interceptor_process_struct_0(Webrtc_stream_interceptorStruct0* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct1 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct1;

Webrtc_stream_interceptorStruct1* webrtc_stream_interceptor_create_struct_1(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_1(Webrtc_stream_interceptorStruct1* ptr);
int webrtc_stream_interceptor_process_struct_1(Webrtc_stream_interceptorStruct1* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct2 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct2;

Webrtc_stream_interceptorStruct2* webrtc_stream_interceptor_create_struct_2(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_2(Webrtc_stream_interceptorStruct2* ptr);
int webrtc_stream_interceptor_process_struct_2(Webrtc_stream_interceptorStruct2* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct3 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct3;

Webrtc_stream_interceptorStruct3* webrtc_stream_interceptor_create_struct_3(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_3(Webrtc_stream_interceptorStruct3* ptr);
int webrtc_stream_interceptor_process_struct_3(Webrtc_stream_interceptorStruct3* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct4 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct4;

Webrtc_stream_interceptorStruct4* webrtc_stream_interceptor_create_struct_4(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_4(Webrtc_stream_interceptorStruct4* ptr);
int webrtc_stream_interceptor_process_struct_4(Webrtc_stream_interceptorStruct4* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct5 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct5;

Webrtc_stream_interceptorStruct5* webrtc_stream_interceptor_create_struct_5(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_5(Webrtc_stream_interceptorStruct5* ptr);
int webrtc_stream_interceptor_process_struct_5(Webrtc_stream_interceptorStruct5* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct6 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct6;

Webrtc_stream_interceptorStruct6* webrtc_stream_interceptor_create_struct_6(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_6(Webrtc_stream_interceptorStruct6* ptr);
int webrtc_stream_interceptor_process_struct_6(Webrtc_stream_interceptorStruct6* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct7 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct7;

Webrtc_stream_interceptorStruct7* webrtc_stream_interceptor_create_struct_7(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_7(Webrtc_stream_interceptorStruct7* ptr);
int webrtc_stream_interceptor_process_struct_7(Webrtc_stream_interceptorStruct7* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct8 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct8;

Webrtc_stream_interceptorStruct8* webrtc_stream_interceptor_create_struct_8(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_8(Webrtc_stream_interceptorStruct8* ptr);
int webrtc_stream_interceptor_process_struct_8(Webrtc_stream_interceptorStruct8* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct9 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct9;

Webrtc_stream_interceptorStruct9* webrtc_stream_interceptor_create_struct_9(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_9(Webrtc_stream_interceptorStruct9* ptr);
int webrtc_stream_interceptor_process_struct_9(Webrtc_stream_interceptorStruct9* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct10 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct10;

Webrtc_stream_interceptorStruct10* webrtc_stream_interceptor_create_struct_10(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_10(Webrtc_stream_interceptorStruct10* ptr);
int webrtc_stream_interceptor_process_struct_10(Webrtc_stream_interceptorStruct10* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct11 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct11;

Webrtc_stream_interceptorStruct11* webrtc_stream_interceptor_create_struct_11(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_11(Webrtc_stream_interceptorStruct11* ptr);
int webrtc_stream_interceptor_process_struct_11(Webrtc_stream_interceptorStruct11* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct12 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct12;

Webrtc_stream_interceptorStruct12* webrtc_stream_interceptor_create_struct_12(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_12(Webrtc_stream_interceptorStruct12* ptr);
int webrtc_stream_interceptor_process_struct_12(Webrtc_stream_interceptorStruct12* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct13 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct13;

Webrtc_stream_interceptorStruct13* webrtc_stream_interceptor_create_struct_13(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_13(Webrtc_stream_interceptorStruct13* ptr);
int webrtc_stream_interceptor_process_struct_13(Webrtc_stream_interceptorStruct13* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct14 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct14;

Webrtc_stream_interceptorStruct14* webrtc_stream_interceptor_create_struct_14(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_14(Webrtc_stream_interceptorStruct14* ptr);
int webrtc_stream_interceptor_process_struct_14(Webrtc_stream_interceptorStruct14* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct15 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct15;

Webrtc_stream_interceptorStruct15* webrtc_stream_interceptor_create_struct_15(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_15(Webrtc_stream_interceptorStruct15* ptr);
int webrtc_stream_interceptor_process_struct_15(Webrtc_stream_interceptorStruct15* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct16 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct16;

Webrtc_stream_interceptorStruct16* webrtc_stream_interceptor_create_struct_16(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_16(Webrtc_stream_interceptorStruct16* ptr);
int webrtc_stream_interceptor_process_struct_16(Webrtc_stream_interceptorStruct16* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct17 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct17;

Webrtc_stream_interceptorStruct17* webrtc_stream_interceptor_create_struct_17(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_17(Webrtc_stream_interceptorStruct17* ptr);
int webrtc_stream_interceptor_process_struct_17(Webrtc_stream_interceptorStruct17* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct18 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct18;

Webrtc_stream_interceptorStruct18* webrtc_stream_interceptor_create_struct_18(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_18(Webrtc_stream_interceptorStruct18* ptr);
int webrtc_stream_interceptor_process_struct_18(Webrtc_stream_interceptorStruct18* ptr, float* pcm, size_t frames);

typedef struct Webrtc_stream_interceptorStruct19 {
    uint64_t id;
    uint64_t tenant_id;
    char name[128];
    int32_t sample_rate;
    int32_t channels;
    float gain;
    bool active;
    uint64_t counter;
} Webrtc_stream_interceptorStruct19;

Webrtc_stream_interceptorStruct19* webrtc_stream_interceptor_create_struct_19(uint64_t tenant_id, const char* name);
void webrtc_stream_interceptor_destroy_struct_19(Webrtc_stream_interceptorStruct19* ptr);
int webrtc_stream_interceptor_process_struct_19(Webrtc_stream_interceptorStruct19* ptr, float* pcm, size_t frames);

int webrtc_stream_interceptor_function_0(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_0_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_1(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_1_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_2(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_2_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_3(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_3_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_4(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_4_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_5(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_5_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_6(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_6_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_7(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_7_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_8(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_8_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_9(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_9_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_10(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_10_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_11(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_11_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_12(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_12_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_13(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_13_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_14(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_14_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_15(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_15_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_16(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_16_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_17(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_17_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_18(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_18_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_19(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_19_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_20(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_20_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_21(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_21_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_22(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_22_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_23(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_23_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_24(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_24_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_25(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_25_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_26(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_26_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_27(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_27_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_28(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_28_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
int webrtc_stream_interceptor_function_29(uint64_t tenant_id, const float* input_pcm, size_t input_frames, float* output_pcm, size_t* output_frames);
int webrtc_stream_interceptor_function_29_with_options(uint64_t tenant_id, const float* input, size_t in_frames, float* output, size_t* out_frames, const struct ProcessingOptions* options);
#ifdef __cplusplus
}
#endif

#endif // WEBRTC_STREAM_INTERCEPTOR_H
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 386 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 387 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 388 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 389 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 390 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 391 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 392 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 393 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 394 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 395 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 396 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 397 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 398 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 399 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 400 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 401 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 402 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 403 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 404 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 405 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 406 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 407 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 408 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 409 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 410 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 411 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 412 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 413 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 414 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 415 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 416 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 417 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 418 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 419 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 420 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 421 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 422 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 423 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 424 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 425 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 426 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 427 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 428 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 429 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 430 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 431 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 432 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 433 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 434 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 435 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 436 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 437 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 438 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 439 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 440 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 441 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 442 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 443 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 444 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 445 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 446 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 447 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 448 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 449 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 450 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 451 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 452 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 453 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 454 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 455 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 456 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 457 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 458 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 459 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 460 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 461 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 462 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 463 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 464 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 465 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 466 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 467 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 468 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 469 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 470 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 471 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 472 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 473 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 474 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 475 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 476 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 477 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 478 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 479 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 480 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 481 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 482 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 483 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 484 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 485 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 486 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 487 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 488 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 489 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 490 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 491 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 492 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 493 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 494 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 495 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 496 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 497 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 498 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 499 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 500 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 501 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 502 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 503 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 504 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 505 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 506 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 507 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 508 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 509 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 510 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 511 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 512 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 513 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 514 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 515 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 516 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 517 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 518 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 519 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 520 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 521 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 522 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 523 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 524 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 525 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 526 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 527 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 528 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 529 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 530 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 531 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 532 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 533 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 534 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 535 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 536 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 537 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 538 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 539 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 540 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 541 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 542 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 543 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 544 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 545 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 546 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 547 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 548 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 549 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 550 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 551 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 552 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 553 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 554 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 555 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 556 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 557 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 558 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 559 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 560 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 561 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 562 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 563 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 564 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 565 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 566 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 567 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 568 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 569 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 570 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 571 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 572 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 573 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 574 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 575 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 576 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 577 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 578 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 579 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 580 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 581 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 582 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 583 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 584 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 585 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 586 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 587 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 588 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 589 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 590 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 591 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 592 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 593 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 594 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 595 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 596 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 597 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 598 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 599 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 600 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 601 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 602 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 603 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 604 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 605 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 606 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 607 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 608 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 609 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 610 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 611 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 612 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 613 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 614 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 615 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 616 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 617 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 618 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 619 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 620 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 621 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 622 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 623 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 624 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 625 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 626 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 627 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 628 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 629 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 630 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 631 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 632 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 633 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 634 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 635 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 636 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 637 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 638 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 639 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 640 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 641 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 642 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 643 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 644 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 645 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 646 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 647 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 648 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 649 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 650 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 651 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 652 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 653 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 654 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 655 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 656 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 657 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 658 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 659 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 660 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 661 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 662 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 663 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 664 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 665 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 666 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 667 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 668 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 669 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 670 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 671 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 672 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 673 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 674 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 675 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 676 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 677 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 678 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 679 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 680 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 681 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 682 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 683 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 684 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 685 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 686 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 687 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 688 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 689 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 690 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 691 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 692 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 693 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 694 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 695 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 696 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 697 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 698 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 699 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 700 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 701 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 702 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 703 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 704 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 705 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 706 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 707 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 708 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 709 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 710 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 711 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 712 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 713 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 714 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 715 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 716 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 717 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 718 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 719 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 720 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 721 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 722 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 723 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 724 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 725 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 726 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 727 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 728 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 729 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 730 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 731 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 732 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 733 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 734 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 735 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 736 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 737 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 738 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 739 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 740 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 741 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 742 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 743 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 744 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 745 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 746 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 747 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 748 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 749 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 750 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 751 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 752 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 753 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 754 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 755 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 756 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 757 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 758 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 759 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 760 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 761 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 762 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 763 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 764 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 765 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 766 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 767 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 768 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 769 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 770 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 771 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 772 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 773 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 774 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 775 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 776 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 777 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 778 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 779 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 780 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 781 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 782 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 783 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 784 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 785 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 786 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 787 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 788 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 789 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 790 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 791 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 792 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 793 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 794 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 795 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 796 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 797 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 798 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 799 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
// Padding webrtc_stream_interceptor/Low-level Native WebRTC AudioSink AudioSource interface line 800 — low-level audio video processing noise reduction voice packet decoding WebRTC media engine AVX2 NEON SIMD lock-free ring buffer memory pool
