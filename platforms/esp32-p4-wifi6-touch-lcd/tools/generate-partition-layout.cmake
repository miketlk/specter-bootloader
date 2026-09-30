if(NOT DEFINED SPECTER_PLATFORM_DIR OR NOT DEFINED SPECTER_LAYOUT_OUTPUT_DIR)
    message(FATAL_ERROR "partition-layout input and output directories are required")
endif()

include("${SPECTER_PLATFORM_DIR}/partition_layout.cmake")
file(MAKE_DIRECTORY "${SPECTER_LAYOUT_OUTPUT_DIR}")

set(SPECTER_PARTITION_CSV "${SPECTER_LAYOUT_OUTPUT_DIR}/partitions.csv")
configure_file(
    "${SPECTER_PLATFORM_DIR}/partitions.csv.in"
    "${SPECTER_PARTITION_CSV}"
    @ONLY)
configure_file(
    "${SPECTER_PLATFORM_DIR}/sdkconfig.defaults.layout.in"
    "${SPECTER_LAYOUT_OUTPUT_DIR}/sdkconfig.defaults"
    @ONLY)
