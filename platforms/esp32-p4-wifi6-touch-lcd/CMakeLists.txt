cmake_minimum_required(VERSION 3.22)

# The shared Specter application is one component. Its explicit dependencies
# keep board networking, camera, audio, and LVGL outside the bootloader image.
if(NOT DEFINED SPECTER_APP)
    set(SPECTER_APP bootloader)
endif()
if(SPECTER_APP STREQUAL "bootloader")
    set(COMPONENTS main)
elseif(SPECTER_APP STREQUAL "mock-main")
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/mock_app")
    set(COMPONENTS mock_app)
else()
    message(FATAL_ERROR "SPECTER_APP must be bootloader or mock-main")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
if(SPECTER_APP STREQUAL "mock-main")
    set(PROJECT_VER "1.0.0")
    project(specter_esp32p4_mock_main)
else()
    project(specter_esp32p4_bootloader)
endif()

include("${CMAKE_CURRENT_LIST_DIR}/partition_layout.cmake")

# Irreversible settings are rejected by default, including when idf.py is
# invoked directly. Only the explicit production profile opts out. Keep this
# list synchronized with the pinned ESP-IDF eFuse startup code.
if(NOT SPECTER_ALLOW_IRREVERSIBLE_CONFIG)
    set(irreversible_configs
        CONFIG_SECURE_FLASH_ENC_ENABLED
        CONFIG_SECURE_BOOT
        CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT
        CONFIG_BOOTLOADER_APP_ANTI_ROLLBACK
        CONFIG_SECURE_DISABLE_ROM_DL_MODE
        CONFIG_SECURE_ENABLE_SECURE_ROM_DL_MODE
        CONFIG_BOOT_ROM_LOG_ALWAYS_OFF
        CONFIG_BOOT_ROM_LOG_ON_GPIO_HIGH
        CONFIG_BOOT_ROM_LOG_ON_GPIO_LOW
        CONFIG_ESP_CRYPTO_FORCE_ECC_CONSTANT_TIME_POINT_MUL
        CONFIG_ESP_ECDSA_ENABLE_P192_CURVE)

    foreach(config IN LISTS irreversible_configs)
        if(${config})
            message(FATAL_ERROR
                "Development builds must not enable irreversible setting ${config}")
        endif()
    endforeach()
endif()

# ESP-IDF normally flashes the application to --partition-boot-default, which
# is always boot_a because it is the factory partition. Phase 2 builds the Root
# Loader for one exact validation target, so keep the standard flash/app-flash
# targets but move their application entry to the matching fixed role.
if(CONFIG_SPECTER_ROOT_VALIDATION_TARGET_BOOT_A)
    if(NOT SPECTER_APP STREQUAL "bootloader")
        message(FATAL_ERROR "boot_a requires the bootloader app")
    endif()
    set(specter_validation_partition boot_a)
    set(specter_expected_validation_offset ${SPECTER_BOOT_A_OFFSET})
elseif(CONFIG_SPECTER_ROOT_VALIDATION_TARGET_BOOT_B)
    if(NOT SPECTER_APP STREQUAL "bootloader")
        message(FATAL_ERROR "boot_b requires the bootloader app")
    endif()
    set(specter_validation_partition boot_b)
    set(specter_expected_validation_offset ${SPECTER_BOOT_B_OFFSET})
elseif(CONFIG_SPECTER_ROOT_VALIDATION_TARGET_MAIN)
    if(NOT SPECTER_APP STREQUAL "mock-main")
        message(FATAL_ERROR "main requires the mock-main app")
    endif()
    set(specter_validation_partition main)
    set(specter_expected_validation_offset ${SPECTER_MAIN_OFFSET})
else()
    message(FATAL_ERROR "No Phase 2 Root Loader validation target is selected")
endif()

partition_table_get_partition_info(
    specter_validation_offset
    "--partition-name ${specter_validation_partition}"
    "offset")
math(EXPR specter_validation_offset_delta
     "${specter_validation_offset} - ${specter_expected_validation_offset}")
if(NOT specter_validation_offset_delta EQUAL 0)
    message(FATAL_ERROR
        "Generated ${specter_validation_partition} offset does not match partition_layout.cmake")
endif()
idf_build_get_property(specter_build_dir BUILD_DIR)
set(specter_project_bin "${PROJECT_BIN}")
if(CONFIG_SPECTER_ROOT_VALIDATION_TARGET_MAIN)
    math(EXPR specter_max_image_size
         "${SPECTER_MAIN_PARTITION_SIZE} - 0x1000")
else()
    math(EXPR specter_max_image_size
         "${SPECTER_BOOTLOADER_PARTITION_SIZE} - 0x1000")
endif()
add_custom_target(specter_app_size_check ALL
    COMMAND ${CMAKE_COMMAND}
        -D "SPECTER_IMAGE=${specter_build_dir}/${specter_project_bin}"
        -D "SPECTER_MAX_IMAGE_SIZE=${specter_max_image_size}"
        -P "${CMAKE_CURRENT_LIST_DIR}/tools/check-app-size.cmake"
    DEPENDS gen_project_binary
    COMMENT "Checking that the ESP image cannot overlap its approval trailer")

if(SPECTER_APP STREQUAL "mock-main")
    add_custom_command(TARGET specter_app_size_check POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E env
            "OBJCOPY=${CMAKE_OBJCOPY}"
            "READELF=${CMAKE_READELF}"
            ${PYTHON} "${CMAKE_CURRENT_LIST_DIR}/mock_app/tools/check_mock_image.py"
            --elf "${specter_build_dir}/${PROJECT_NAME}.elf"
            --bin "${specter_build_dir}/${specter_project_bin}"
            --map "${specter_build_dir}/${PROJECT_NAME}.map"
            --manifest "${specter_build_dir}/mock-manifest.json"
            --board "${SPECTER_BOARD_PROFILE}"
            --requested "${SPECTER_MOCK_BLOAT_BYTES}"
            --partition-size "${SPECTER_MAIN_PARTITION_SIZE}"
            --trailer-size "${SPECTER_ESP32P4_TRAILER_SIZE}"
            --project-version "${PROJECT_VER}"
            --idf-version "${IDF_VER}"
        COMMENT "Validating retained mock section and writing build manifest")
endif()

function(specter_remove_default_app target_name)
    foreach(property_name FLASH_FILE FLASH_ENTRY IMAGES)
        get_property(property_values TARGET ${target_name} PROPERTY ${property_name})
        set(filtered_values)
        foreach(property_value IN LISTS property_values)
            string(FIND "${property_value}" "${specter_project_bin}" app_entry)
            if(app_entry EQUAL -1)
                list(APPEND filtered_values "${property_value}")
            endif()
        endforeach()
        set_property(TARGET ${target_name} PROPERTY ${property_name}
                     "${filtered_values}")
    endforeach()
endfunction()

foreach(flash_target flash app-flash)
    specter_remove_default_app(${flash_target})
    esptool_py_flash_target_image(
        ${flash_target} app "${specter_validation_offset}"
        "${specter_build_dir}/${specter_project_bin}" ALWAYS_PLAINTEXT)
endforeach()
