set(repo_root "${CMAKE_CURRENT_LIST_DIR}/../../..")
set(platform_dir "${CMAKE_CURRENT_LIST_DIR}/..")

if(NOT DEFINED SPECTER_KEYS)
    set(SPECTER_KEYS test)
endif()
if(NOT SPECTER_KEYS MATCHES "^(test|selfsigned|production)$")
    message(FATAL_ERROR "SPECTER_KEYS must be test, selfsigned, or production")
endif()
set(key_source "${repo_root}/keys/${SPECTER_KEYS}/pubkeys.c")
if(NOT EXISTS "${key_source}")
    message(FATAL_ERROR "Missing public-key source ${key_source}")
endif()

set(core_sources
    "${repo_root}/core/bl_integrity_check.c"
    "${repo_root}/core/bl_kats.c"
    "${repo_root}/core/bl_section.c"
    "${repo_root}/core/bl_signature.c"
    "${repo_root}/core/bl_util.c"
    "${repo_root}/core/bootloader.c")
set(platform_sources
    "${platform_dir}/common/bl_syscalls_esp32p4.c"
    "${platform_dir}/common/esp32p4_flash_map.c"
    "${platform_dir}/common/esp32p4_gui.c"
    "${platform_dir}/common/esp32p4_media.c"
    "${platform_dir}/common/esp32p4_reset.c"
    "${platform_dir}/common/esp32p4_trailer.c")

if(CONFIG_SPECTER_BOARD_LCD_4P3)
    list(APPEND platform_sources
        "${platform_dir}/lcd-4p3/board_display.c")
    set(board_include_dir "${platform_dir}/lcd-4p3")
else()
    list(APPEND platform_sources
        "${platform_dir}/common/esp32p4_board_stub.c")
    set(board_include_dir "${platform_dir}/common")
endif()

idf_component_register(
    SRCS
        "main.c"
        ${core_sources}
        ${platform_sources}
        "${repo_root}/core/secp256k1_add/ext_callbacks.c"
        "${repo_root}/lib/bech32/segwit_addr.c"
        "${repo_root}/lib/crc32/crc32.c"
        "${repo_root}/lib/crypto/memzero.c"
        "${repo_root}/lib/crypto/sha2.c"
        "${repo_root}/lib/secp256k1/src/secp256k1.c"
        "${key_source}"
    INCLUDE_DIRS
        "."
        "${board_include_dir}"
        "${platform_dir}/common"
        "${repo_root}/core"
        "${repo_root}/core/secp256k1_add"
        "${repo_root}/lib/bech32"
        "${repo_root}/lib/crc32"
        "${repo_root}/lib/crypto"
        "${repo_root}/lib/secp256k1"
        "${repo_root}/lib/secp256k1/include"
        "${repo_root}/lib/secp256k1/src"
    REQUIRES
        app_update
        bootloader_support
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_ledc
        esp_hw_support
        esp_lcd
        esp_partition
        fatfs
        nvs_flash
        nvs_sec_provider
        sdmmc
        spi_flash
    # The core supplies weak platform defaults in the same archive as strong
    # platform/key overrides. Retain every application object so the linker
    # resolves those overrides instead of satisfying references internally
    # from the weak defaults.
    WHOLE_ARCHIVE)

include("${platform_dir}/partition_layout.cmake")
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    BL_NO_FATFS
    CRC32_USE_LOOKUP_TABLE_SLICING_BY_8
    HAVE_CONFIG_H
    SECP256K1_BUILD
    __BYTE_ORDER=1234
    SPECTER_BOOTLOADER_PARTITION_SIZE=${SPECTER_BOOTLOADER_PARTITION_SIZE}
    SPECTER_BOOT_A_OFFSET=${SPECTER_BOOT_A_OFFSET}
    SPECTER_BOOT_B_OFFSET=${SPECTER_BOOT_B_OFFSET}
    SPECTER_MAIN_OFFSET=${SPECTER_MAIN_OFFSET}
    SPECTER_MAIN_PARTITION_SIZE=${SPECTER_MAIN_PARTITION_SIZE})

# The bundled secp256k1 version intentionally has legacy declarations which
# newer GCC diagnoses more aggressively. Keep warnings enabled for port code.
set_source_files_properties(
    "${repo_root}/lib/secp256k1/src/secp256k1.c"
    PROPERTIES COMPILE_OPTIONS "-Wno-unused-function;-Wno-maybe-uninitialized")
set_source_files_properties(
    "${repo_root}/lib/crypto/sha2.c"
    PROPERTIES COMPILE_OPTIONS "-Wno-error=array-parameter")
