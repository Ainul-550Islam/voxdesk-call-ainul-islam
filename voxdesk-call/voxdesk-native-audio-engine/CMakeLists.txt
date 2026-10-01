cmake_minimum_required(VERSION 3.22)
project(voxdesk-native-audio-engine VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 11)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# AVX2/NEON flags, GoogleTest, & FFI targets
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64")
    set(ARCH_FLAGS "-mavx2 -mfma -msse4.2 -mpopcnt")
    add_definitions(-DUSE_AVX2 -DUSE_SSE42)
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(ARCH_FLAGS "-march=armv8-a+simd -mfpu=neon")
    add_definitions(-DUSE_NEON -DUSE_ARM_SIMD)
endif()

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${ARCH_FLAGS} -O3 -ffast-math -funroll-loops -flto -DNDEBUG")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${ARCH_FLAGS} -O3 -ffast-math -funroll-loops -flto -DNDEBUG")
set(CMAKE_CXX_FLAGS_DEBUG "-O0 -g -DDEBUG -fsanitize=address,undefined")
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG -flto -ffast-math")

# Dependencies
find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)

# Opus
pkg_check_modules(OPUS opus)
# Include dirs
include_directories(${CMAKE_SOURCE_DIR}/include)
include_directories(${CMAKE_SOURCE_DIR}/third_party/opus/include)
include_directories(${CMAKE_SOURCE_DIR}/third_party/speexdsp/include)
include_directories(${CMAKE_SOURCE_DIR}/third_party/rnnoise/include)
include_directories(${CMAKE_SOURCE_DIR}/third_party/pybind11/include)

# GoogleTest
add_subdirectory(third_party/googletest EXCLUDE_FROM_ALL)

# Core library — 5-15MB
file(GLOB_RECURSE CORE_SOURCES
    src/dsp/*.cpp
    src/codec/*.cpp
    src/webrtc/*.cpp
    src/memory/*.cpp
    src/utils/*.cpp
    src/ffi/c_api.cpp
)

add_library(voxdesk_audio_core STATIC ${CORE_SOURCES})
target_link_libraries(voxdesk_audio_core PUBLIC Threads::Threads ${OPUS_LIBRARIES} m)
target_include_directories(voxdesk_audio_core PUBLIC include third_party/opus/include)

# Shared library for FFI (C-ABI for Rust/Python)
add_library(voxdesk_audio_shared SHARED ${CORE_SOURCES})
target_link_libraries(voxdesk_audio_shared PUBLIC Threads::Threads ${OPUS_LIBRARIES} m)
set_target_properties(voxdesk_audio_shared PROPERTIES
    VERSION ${PROJECT_VERSION}
    SOVERSION 1
    OUTPUT_NAME voxdesk_audio
)

# CLI benchmarking tool
add_executable(voxdesk_audio_cli src/main.cpp)
target_link_libraries(voxdesk_audio_cli PRIVATE voxdesk_audio_core)

# Python module via pybind11
if(EXISTS ${CMAKE_SOURCE_DIR}/third_party/pybind11/CMakeLists.txt)
    add_subdirectory(third_party/pybind11)
    pybind11_add_module(voxdesk_audio_python src/ffi/python_module.cpp)
    target_link_libraries(voxdesk_audio_python PRIVATE voxdesk_audio_core)
endif()

# Tests
enable_testing()
file(GLOB TEST_SOURCES tests/*.cpp)
foreach(TEST_SRC ${TEST_SOURCES})
    get_filename_component(TEST_NAME ${TEST_SRC} NAME_WE)
    add_executable(${TEST_NAME} ${TEST_SRC})
    target_link_libraries(${TEST_NAME} PRIVATE voxdesk_audio_core gtest_main gmock)
    add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
endforeach()

# Install
install(TARGETS voxdesk_audio_core voxdesk_audio_shared voxdesk_audio_cli
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)
install(DIRECTORY include/ DESTINATION include)

# CPack
set(CPACK_PACKAGE_NAME "voxdesk-native-audio-engine")
set(CPACK_PACKAGE_VERSION ${PROJECT_VERSION})
set(CPACK_GENERATOR "TGZ;DEB")
include(CPack)
