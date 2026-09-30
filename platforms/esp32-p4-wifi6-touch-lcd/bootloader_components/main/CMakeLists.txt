idf_component_register(SRCS "bootloader_start.c"
                       REQUIRES bootloader bootloader_support spi_flash
                                esp_app_format)

include("${CMAKE_CURRENT_LIST_DIR}/../../partition_layout.cmake")
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    SPECTER_ROOT_LOADER_PARTITION_SIZE=${SPECTER_ROOT_LOADER_PARTITION_SIZE}
    SPECTER_BOOTLOADER_PARTITION_SIZE=${SPECTER_BOOTLOADER_PARTITION_SIZE}
    SPECTER_BOOT_A_OFFSET=${SPECTER_BOOT_A_OFFSET}
    SPECTER_BOOT_B_OFFSET=${SPECTER_BOOT_B_OFFSET}
    SPECTER_MAIN_OFFSET=${SPECTER_MAIN_OFFSET}
    SPECTER_MAIN_PARTITION_SIZE=${SPECTER_MAIN_PARTITION_SIZE})

# Reuse the linker scripts selected by the pinned ESP-IDF bootloader project.
idf_build_get_property(scripts BOOTLOADER_LINKER_SCRIPT)
target_linker_script(${COMPONENT_LIB} INTERFACE "${scripts}")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u bootloader_hooks_include")
