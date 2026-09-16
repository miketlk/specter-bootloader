set(repo_root "${CMAKE_CURRENT_LIST_DIR}/../../../..")
set(platform_common "${CMAKE_CURRENT_LIST_DIR}/../../common")

idf_component_register(SRCS "bootloader_start.c"
                            "${repo_root}/lib/crc32/crc32.c"
                       INCLUDE_DIRS "${platform_common}"
                                    "${repo_root}/lib/crc32"
                       REQUIRES bootloader bootloader_support spi_flash
                                esp_app_format)

include("${CMAKE_CURRENT_LIST_DIR}/../../partition_layout.cmake")
if("$ENV{SPECTER_BOARD}" STREQUAL "4p3")
    set(specter_root_board_definition SPECTER_ROOT_BOARD_4P3=1)
elseif("$ENV{SPECTER_BOARD}" STREQUAL "5")
    set(specter_root_board_definition SPECTER_ROOT_BOARD_5=1)
else()
    message(FATAL_ERROR "SPECTER_BOARD must select the Root Loader platform ID")
endif()
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    SPECTER_ROOT_LOADER_PARTITION_SIZE=${SPECTER_ROOT_LOADER_PARTITION_SIZE}
    SPECTER_BOOTLOADER_PARTITION_SIZE=${SPECTER_BOOTLOADER_PARTITION_SIZE}
    SPECTER_BOOT_A_OFFSET=${SPECTER_BOOT_A_OFFSET}
    SPECTER_BOOT_B_OFFSET=${SPECTER_BOOT_B_OFFSET}
    SPECTER_MAIN_OFFSET=${SPECTER_MAIN_OFFSET}
    SPECTER_MAIN_PARTITION_SIZE=${SPECTER_MAIN_PARTITION_SIZE}
    CRC32_USE_LOOKUP_TABLE_SLICING_BY_8
    __BYTE_ORDER=1234
    ${specter_root_board_definition})

# Reuse the linker scripts selected by the pinned ESP-IDF bootloader project.
idf_build_get_property(scripts BOOTLOADER_LINKER_SCRIPT)
target_linker_script(${COMPONENT_LIB} INTERFACE "${scripts}")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u bootloader_hooks_include")
