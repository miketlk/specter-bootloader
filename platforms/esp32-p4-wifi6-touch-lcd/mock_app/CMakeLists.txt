set(mock_dir "${COMPONENT_DIR}")
set(repo_root "${mock_dir}/../../..")
set(platform_dir "${mock_dir}/..")

if(NOT DEFINED SPECTER_MOCK_BLOAT_BYTES)
    set(SPECTER_MOCK_BLOAT_BYTES 0)
endif()
if(NOT SPECTER_MOCK_BLOAT_BYTES MATCHES "^[0-9]+$")
    message(FATAL_ERROR "SPECTER_MOCK_BLOAT_BYTES must be unsigned base-10")
endif()
include("${platform_dir}/partition_layout.cmake")
math(EXPR specter_mock_payload_capacity
     "${SPECTER_MAIN_PARTITION_SIZE} - ${SPECTER_ESP32P4_TRAILER_SIZE}")
if(SPECTER_MOCK_BLOAT_BYTES GREATER specter_mock_payload_capacity)
    message(FATAL_ERROR
        "requested mock bloat alone exceeds main payload capacity")
endif()

set(generated_bloat "${CMAKE_CURRENT_BINARY_DIR}/mock_bloat.S")
configure_file("${mock_dir}/mock_bloat.S.in" "${generated_bloat}" @ONLY)

set(mock_sources
    "${mock_dir}/mock_main.c"
    "${mock_dir}/mock_screen.c"
    "${mock_dir}/mock_status.c"
    "${mock_dir}/mock_telemetry.c"
    "${generated_bloat}"
    "${platform_dir}/common/display_hal.c"
    "${repo_root}/lib/crc32/crc32.c"
    "${repo_root}/platforms/stm32f469disco/Utilities/Fonts/font12.c"
    "${repo_root}/platforms/stm32f469disco/Utilities/Fonts/font20.c")

if("$ENV{SPECTER_BOARD}" STREQUAL "4p3")
    list(APPEND mock_sources "${platform_dir}/lcd-4p3/board_display.c")
    set(board_include_dir "${platform_dir}/lcd-4p3")
elseif("$ENV{SPECTER_BOARD}" STREQUAL "5")
    list(APPEND mock_sources
        "${platform_dir}/lcd-5/board_display.c"
        "${platform_dir}/lcd-5/esp_lcd_hx8394.c")
    set(board_include_dir "${platform_dir}/lcd-5")
else()
    message(FATAL_ERROR "mock-main requires a physical board profile")
endif()

idf_component_register(
    SRCS ${mock_sources}
    INCLUDE_DIRS
        "${mock_dir}"
        "${board_include_dir}"
        "${platform_dir}/common"
        "${repo_root}/core"
        "${repo_root}/lib/crc32"
        "${repo_root}/platforms/stm32f469disco/Utilities/Fonts"
    REQUIRES
        app_update
        bootloader_support
        cbor
        esp_driver_gpio
        esp_driver_ledc
        esp_driver_uart
        esp_hw_support
        esp_lcd
        esp_partition
        esp_psram
        esp_timer
        spi_flash
    LDFRAGMENTS "mock_bloat.lf")

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    __BYTE_ORDER=1234
    SPECTER_BOOTLOADER_PARTITION_SIZE=${SPECTER_BOOTLOADER_PARTITION_SIZE}
    SPECTER_BOOT_A_OFFSET=${SPECTER_BOOT_A_OFFSET}
    SPECTER_BOOT_B_OFFSET=${SPECTER_BOOT_B_OFFSET}
    SPECTER_MAIN_OFFSET=${SPECTER_MAIN_OFFSET}
    SPECTER_MAIN_PARTITION_SIZE=${SPECTER_MAIN_PARTITION_SIZE}
    SPECTER_MOCK_BLOAT_BYTES=${SPECTER_MOCK_BLOAT_BYTES})
