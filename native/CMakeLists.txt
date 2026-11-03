cmake_minimum_required(VERSION 3.24)
project(SoundboxDeck LANGUAGES C CXX OBJCXX)

if(NOT APPLE)
    message(FATAL_ERROR "The Soundbox native deck currently targets macOS only")
endif()

set(CMAKE_OSX_ARCHITECTURES arm64)
set(CMAKE_OSX_DEPLOYMENT_TARGET 14.0)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(soundbox_process_guard SHARED ProcessGuard.c)
target_compile_options(soundbox_process_guard PRIVATE -Wall -Wextra -Wpedantic -Werror)
set_target_properties(soundbox_process_guard PROPERTIES
    OUTPUT_NAME soundbox_process_guard
    C_VISIBILITY_PRESET hidden
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    MACOSX_RPATH ON
)

add_library(soundbox_deck SHARED
    SoundboxDeck.mm
)

target_include_directories(soundbox_deck PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(soundbox_deck PRIVATE
    -fobjc-arc
    -Wall
    -Wextra
    -Wpedantic
)
target_link_libraries(soundbox_deck PRIVATE
    "-framework AppKit"
    "-framework AudioToolbox"
    "-framework AVFAudio"
    "-framework CoreAudio"
    "-framework Metal"
    "-framework QuartzCore"
)

set_target_properties(soundbox_deck PROPERTIES
    OUTPUT_NAME soundbox_deck
    MACOSX_RPATH ON
)
