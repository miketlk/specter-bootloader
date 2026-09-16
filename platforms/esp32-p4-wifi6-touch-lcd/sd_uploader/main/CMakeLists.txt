idf_component_register(SRCS "sd_uploader_main.c" "protocol.c" "storage.c" "session.c" "ram_reservations.c" "ram_dma.c" REQUIRES esp_driver_gpio esp_mm fatfs esp_driver_uart mbedtls esp_timer)

file(GLOB SDU_SOURCES "${CMAKE_CURRENT_LIST_DIR}/*.c" "${CMAKE_CURRENT_LIST_DIR}/*.h")
list(APPEND SDU_SOURCES
  "${CMAKE_CURRENT_LIST_DIR}/../../lcd-4p3/board_config.h"
  "${CMAKE_CURRENT_LIST_DIR}/../../lcd-5/board_config.h"
  "${SDKCONFIG}")
set(IDENTITY "")
foreach(SOURCE IN LISTS SDU_SOURCES)
  file(SHA256 "${SOURCE}" DIGEST)
  string(APPEND IDENTITY "${DIGEST}")
endforeach()
string(SHA256 SDU_BUILD_ID "${IDENTITY}")
target_compile_definitions(${COMPONENT_LIB} PRIVATE SDU_BUILD_ID="${SDU_BUILD_ID}")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${SDU_SOURCES})

target_link_options(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=esp_heap_adjust_alignment_to_hw")
