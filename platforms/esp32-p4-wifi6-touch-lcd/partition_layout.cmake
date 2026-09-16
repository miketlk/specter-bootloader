# Canonical ESP32-P4 application partition sizes. Derive offsets and generated
# partition data from these values; do not duplicate their numeric values.
set(SPECTER_ROOT_LOADER_PARTITION_SIZE 0x20000)
set(SPECTER_BOOTLOADER_PARTITION_SIZE 0x100000)
set(SPECTER_MAIN_PARTITION_SIZE 0x400000)
file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/common/esp32p4_boot_contract.h"
     specter_trailer_definition
     REGEX "^#define SPECTER_ESP32P4_TRAILER_SIZE 0x[0-9A-Fa-f]+U$")
string(REGEX MATCH "0x[0-9A-Fa-f]+" SPECTER_ESP32P4_TRAILER_SIZE
       "${specter_trailer_definition}")
if(NOT SPECTER_ESP32P4_TRAILER_SIZE)
    message(FATAL_ERROR "cannot derive approval trailer size from platform header")
endif()
# A zero-sized optional partition is a disabled layout provision. The layout
# generator drops it from the ESP-IDF partition CSV rather than emitting a
# zero-sized entry, which ESP-IDF could interpret incorrectly.
set(SPECTER_MAIN_AUX_PARTITION_SIZE 0x0)

math(EXPR SPECTER_BOOT_A_OFFSET
     "${SPECTER_ROOT_LOADER_PARTITION_SIZE}"
     OUTPUT_FORMAT HEXADECIMAL)
math(EXPR SPECTER_BOOT_B_OFFSET
     "${SPECTER_ROOT_LOADER_PARTITION_SIZE} + ${SPECTER_BOOTLOADER_PARTITION_SIZE}"
     OUTPUT_FORMAT HEXADECIMAL)
math(EXPR SPECTER_MAIN_OFFSET
     "${SPECTER_ROOT_LOADER_PARTITION_SIZE} + (2 * ${SPECTER_BOOTLOADER_PARTITION_SIZE})"
     OUTPUT_FORMAT HEXADECIMAL)
math(EXPR SPECTER_MAIN_AUX_OFFSET
     "${SPECTER_MAIN_OFFSET} + ${SPECTER_MAIN_PARTITION_SIZE}"
     OUTPUT_FORMAT HEXADECIMAL)

foreach(app_offset IN ITEMS
        SPECTER_BOOT_A_OFFSET SPECTER_BOOT_B_OFFSET SPECTER_MAIN_OFFSET)
    math(EXPR app_alignment_remainder "${${app_offset}} % 0x10000")
    if(NOT app_alignment_remainder EQUAL 0)
        message(FATAL_ERROR "${app_offset} must be 64 KiB aligned")
    endif()
endforeach()
