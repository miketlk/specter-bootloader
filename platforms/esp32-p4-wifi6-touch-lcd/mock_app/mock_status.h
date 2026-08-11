/**
 * @file mock_status.h
 * @brief Immutable status snapshot for the ESP32-P4 mock Main Firmware.
 */

#ifndef SPECTER_MOCK_STATUS_H_INCLUDED
#define SPECTER_MOCK_STATUS_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

#include "esp32p4_trailer.h"

#define SPECTER_MOCK_NAME "Mock Main Firmware"
#define SPECTER_MOCK_SCHEMA_VERSION 1U
#define SPECTER_MOCK_CBOR_MAX 8192U
#define SPECTER_MOCK_BLOAT_SECTION ".specter_mock_bloat"
#define SPECTER_MOCK_BLOAT_FILL 0xA5U

typedef enum specter_mock_check {
  specter_mock_check_not_run,
  specter_mock_check_pass,
  specter_mock_check_fail,
  specter_mock_check_unavailable,
} specter_mock_check_t;

typedef enum specter_mock_approval_state {
  specter_mock_approval_absent,
  specter_mock_approval_present,
  specter_mock_approval_valid,
  specter_mock_approval_read_error,
} specter_mock_approval_state_t;

typedef struct specter_mock_status {
  const char* app_name;
  const char* project_version;
  const char* idf_version;
  const char* platform_id;
  const char* board_profile;
  const char* partition_label;
  uint32_t chip_model;
  uint32_t chip_revision;
  uint32_t configured_flash_size;
  uint32_t detected_flash_size;
  uint32_t detected_psram_size;
  uint32_t display_width;
  uint32_t display_height;
  uint32_t requested_bloat;
  uint32_t linked_bloat;
  uint32_t image_length;
  uint8_t image_digest[32];
  bool image_digest_present;
  uint32_t partition_type;
  uint32_t partition_subtype;
  uint32_t partition_address;
  uint32_t partition_size;
  uint32_t payload_capacity;
  uint32_t trailer_address;
  uint32_t trailer_size;
  uint32_t remaining_bytes;
  specter_mock_approval_state_t approval_state;
  specter_approval_record_t approval;
  specter_mock_check_t main_role_check;
  specter_mock_check_t image_check;
  specter_mock_check_t bloat_check;
  specter_mock_check_t approval_check;
  specter_mock_check_t approval_digest_check;
  uint32_t reset_reason;
  uint64_t session_id;
  bool ui_ready;
  int32_t ui_error;
  bool telemetry_ready;
  int32_t telemetry_error;
} specter_mock_status_t;

const char* specter_mock_check_text(specter_mock_check_t check);
const char* specter_mock_approval_state_text(
    specter_mock_approval_state_t state);
bool specter_mock_status_collect(specter_mock_status_t* status);

#endif  // SPECTER_MOCK_STATUS_H_INCLUDED
