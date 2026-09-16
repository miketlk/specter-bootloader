cmake_minimum_required(VERSION 3.16)
get_filename_component(REPO "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
get_filename_component(ACTUAL_IDF "$ENV{IDF_PATH}" REALPATH)
if(NOT ACTUAL_IDF STREQUAL "${REPO}/third_party/esp-idf")
  message(FATAL_ERROR "Use the repository pinned ESP-IDF")
endif()
set(COMPONENTS main)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(sd-uploader)
if(NOT IDF_TARGET STREQUAL "esp32p4" OR NOT CONFIG_APP_BUILD_TYPE_PURE_RAM_APP OR
   NOT CONFIG_APP_BUILD_TYPE_RAM OR NOT CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT OR CONFIG_APP_BUILD_USE_FLASH_SECTIONS OR
   CONFIG_APP_BUILD_BOOTLOADER OR CONFIG_SPIRAM OR CONFIG_SECURE_BOOT OR
   CONFIG_SECURE_FLASH_ENC_ENABLED OR CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS)
  message(FATAL_ERROR "Uploader requires reversible plaintext pure internal RAM configuration")
endif()
if(NOT CONFIG_ESP32P4_SELECTS_REV_LESS_V3)
  message(FATAL_ERROR "Uploader currently supports revision 1.x only")
endif()
# Derive the layout from pinned scripts: high SRAM holds code and buffers,
# low SRAM holds data. IDF initializes BSS and excludes every allocation from
# its runtime heaps; no external or retained-memory segments are permitted.
set(SDU_LD "${CMAKE_BINARY_DIR}/sdu-sections.ld")
if(DEFINED SDU_REQUESTED_BAUD AND NOT CONFIG_SDU_UART_BAUD EQUAL SDU_REQUESTED_BAUD)
  message(FATAL_ERROR "Stale UART baud setting: use the matching isolated profile")
endif()
set(IDF_LD "${CMAKE_BINARY_DIR}/esp-idf/esp_system/ld/sections.ld")
add_custom_command(OUTPUT "${SDU_LD}"
  COMMAND ${CMAKE_COMMAND} -DINPUT=${IDF_LD} -DOUTPUT=${SDU_LD}
    -P "${CMAKE_CURRENT_LIST_DIR}/ld/place-buffers.cmake"
  DEPENDS "${IDF_LD}" "${CMAKE_CURRENT_LIST_DIR}/ld/place-buffers.cmake")
add_custom_target(sdu_linker DEPENDS "${SDU_LD}")
add_dependencies(sdu_linker __ldgen_output_sections.ld)
add_dependencies(sd-uploader.elf sdu_linker)
get_target_property(SYSTEM_OPTIONS __idf_esp_system INTERFACE_LINK_OPTIONS)
list(TRANSFORM SYSTEM_OPTIONS REPLACE "SHELL:-T sections.ld" "SHELL:-T ${SDU_LD}")
list(REMOVE_ITEM SYSTEM_OPTIONS "-Wl,--enable-non-contiguous-regions")
set_property(TARGET __idf_esp_system PROPERTY INTERFACE_LINK_OPTIONS "${SYSTEM_OPTIONS}")
set_property(TARGET sd-uploader.elf APPEND PROPERTY LINK_DEPENDS "${SDU_LD}")
add_custom_target(sdu_forbid_deployment
  COMMAND ${CMAKE_COMMAND} -P "${CMAKE_CURRENT_LIST_DIR}/ld/no-deployment.cmake")
foreach(DEPLOY flash app-flash erase_flash bootloader-flash partition-table-flash encrypted-flash encrypted-app-flash merge-bin)
  if(TARGET ${DEPLOY})
    add_dependencies(${DEPLOY} sdu_forbid_deployment)
  endif()
endforeach()
foreach(UNSAFE ESP_SYSTEM_PMP_IDRAM_SPLIT ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP
    ESP_COREDUMP_ENABLE_TO_FLASH SECURE_BOOT_ENABLE_AGGRESSIVE_KEY_REVOKE
    BOOTLOADER_APP_ANTI_ROLLBACK BOOTLOADER_EFUSE_SECURE_VERSION_EMULATE)
  if(CONFIG_${UNSAFE})
    message(FATAL_ERROR "Unsupported RAM uploader configuration: ${UNSAFE}")
  endif()
endforeach()
if(CONFIG_SDU_BOARD_4P3)
  set(SDU_BOARD lcd-4p3)
else()
  set(SDU_BOARD lcd-5)
endif()
add_custom_target(sdu_ram_audit ALL
  COMMAND ${CMAKE_COMMAND} -E env "PYTHONPATH=${REPO}/tools" ${PYTHON}
    -m sd_uploader.ram_image --elf "${CMAKE_BINARY_DIR}/sd-uploader.elf"
    --image "${CMAKE_BINARY_DIR}/sd-uploader.bin" --sdkconfig "${SDKCONFIG}"
    --board ${SDU_BOARD} --output "${CMAKE_BINARY_DIR}/ram-manifest.json"
  DEPENDS gen_project_binary
  VERBATIM)
