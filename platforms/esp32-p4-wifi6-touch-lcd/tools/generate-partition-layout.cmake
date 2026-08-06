if(NOT DEFINED SPECTER_PLATFORM_DIR OR NOT DEFINED SPECTER_LAYOUT_OUTPUT_DIR)
    message(FATAL_ERROR "partition-layout input and output directories are required")
endif()

include("${SPECTER_PLATFORM_DIR}/partition_layout.cmake")
file(MAKE_DIRECTORY "${SPECTER_LAYOUT_OUTPUT_DIR}")

# ESP-IDF partition CSVs do not have a portable zero-size "disabled" form.
# Drop an optional partition whose canonical size is zero; emit it only after
# a future layout change assigns it real space.
set(SPECTER_MAIN_AUX_PARTITION "")
if(SPECTER_MAIN_AUX_PARTITION_SIZE GREATER 0)
    set(SPECTER_MAIN_AUX_PARTITION
        "main_aux,   data, 0x40,    ${SPECTER_MAIN_AUX_OFFSET}, ${SPECTER_MAIN_AUX_PARTITION_SIZE}, encrypted")
endif()

set(SPECTER_PARTITION_CSV "${SPECTER_LAYOUT_OUTPUT_DIR}/partitions.csv")
configure_file(
    "${SPECTER_PLATFORM_DIR}/partitions.csv.in"
    "${SPECTER_PARTITION_CSV}"
    @ONLY)
file(READ "${SPECTER_PARTITION_CSV}" partition_csv_contents)
string(REGEX REPLACE "\n+$" "\n" partition_csv_contents
       "${partition_csv_contents}")
file(WRITE "${SPECTER_PARTITION_CSV}" "${partition_csv_contents}")
# The checked-in map is reviewable release input. Keep it mechanically equal
# to the generated layout so changes cannot silently drift from compiled data.
execute_process(
    COMMAND "${CMAKE_COMMAND}" -E compare_files
            "${SPECTER_PARTITION_CSV}"
            "${SPECTER_PLATFORM_DIR}/partitions.csv"
    RESULT_VARIABLE partition_map_differs)
if(partition_map_differs)
    message(FATAL_ERROR
        "generated partition layout differs from checked-in partitions.csv")
endif()
file(WRITE "${SPECTER_LAYOUT_OUTPUT_DIR}/layout.metadata"
    "main_aux_size=${SPECTER_MAIN_AUX_PARTITION_SIZE}\n")
configure_file(
    "${SPECTER_PLATFORM_DIR}/sdkconfig.defaults.layout.in"
    "${SPECTER_LAYOUT_OUTPUT_DIR}/sdkconfig.defaults"
    @ONLY)
