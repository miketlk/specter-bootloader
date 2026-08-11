/**
 * @file mock_status.c
 * @brief Status collection for the ESP32-P4 mock Main Firmware.
 */

#include "mock_status.h"

#include <stddef.h>
#include <string.h>

#include "bl_util.h"
#include "board_config.h"
#include "crc32.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_image_format.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_psram.h"
#include "esp_random.h"
#include "esp_system.h"

extern const uint8_t specter_mock_bloat_start[];
extern const uint8_t specter_mock_bloat_end[];

static bool erased_record(const specter_approval_record_t* record) {
  const uint8_t* bytes = (const uint8_t*)record;
  for (size_t index = 0; index < sizeof(*record); ++index) {
    if (bytes[index] != 0xffU) {
      return false;
    }
  }
  return true;
}

static bool valid_record(const specter_approval_record_t* record,
                         const esp_partition_t* running) {
  return 0 == memcmp(record->magic, SPECTER_APPROVAL_MAGIC,
                     sizeof(record->magic)) &&
         record->revision == SPECTER_APPROVAL_REVISION &&
         record->record_size == sizeof(*record) &&
         memchr(record->platform, '\0', sizeof(record->platform)) &&
         0 == strncmp(record->platform,
#if CONFIG_SPECTER_BOARD_LCD_4P3
                      "esp32-p4-wifi6-touch-lcd-4p3"
#else
                      "esp32-p4-wifi6-touch-lcd-5"
#endif
                      ,
                      sizeof(record->platform)) &&
         record->role == specter_role_main &&
         record->semantic_version > BL_VERSION_NA &&
         record->semantic_version <= BL_VERSION_MAX &&
         record->image_length > 0U &&
         record->image_length <= running->size - SPECTER_ESP32P4_TRAILER_SIZE &&
         record->sequence > 0U &&
         record->status == SPECTER_APPROVAL_STATUS_APPROVED &&
         record->commit_crc ==
             crc32_fast(record, offsetof(specter_approval_record_t, commit_crc),
                        0U);
}

const char* specter_mock_check_text(specter_mock_check_t check) {
  switch (check) {
    case specter_mock_check_pass:
      return "pass";
    case specter_mock_check_fail:
      return "fail";
    case specter_mock_check_unavailable:
      return "unavailable";
    default:
      return "not_run";
  }
}

const char* specter_mock_approval_state_text(
    specter_mock_approval_state_t state) {
  switch (state) {
    case specter_mock_approval_absent:
      return "absent";
    case specter_mock_approval_present:
      return "present";
    case specter_mock_approval_valid:
      return "valid";
    default:
      return "read_error";
  }
}

bool specter_mock_status_collect(specter_mock_status_t* status) {
  if (!status) {
    return false;
  }
  memset(status, 0, sizeof(*status));
  status->app_name = SPECTER_MOCK_NAME;
  const esp_app_desc_t* description = esp_app_get_description();
  status->project_version = description->version;
  status->idf_version = description->idf_ver;
#if CONFIG_SPECTER_BOARD_LCD_4P3
  status->platform_id = "esp32-p4-wifi6-touch-lcd-4p3";
#else
  status->platform_id = "esp32-p4-wifi6-touch-lcd-5";
#endif
  status->board_profile = SPECTER_BOARD_PROFILE;
  status->configured_flash_size = 16U * 1024U * 1024U;
  status->detected_psram_size = (uint32_t)esp_psram_get_size();
  status->display_width = SPECTER_LCD_WIDTH;
  status->display_height = SPECTER_LCD_HEIGHT;
  status->requested_bloat = SPECTER_MOCK_BLOAT_BYTES;
  status->linked_bloat = (uint32_t)((uintptr_t)specter_mock_bloat_end -
                                    (uintptr_t)specter_mock_bloat_start);
  status->bloat_check = status->requested_bloat == status->linked_bloat
                            ? specter_mock_check_pass
                            : specter_mock_check_fail;
  status->reset_reason = esp_reset_reason();
  esp_fill_random(&status->session_id, sizeof(status->session_id));

  esp_chip_info_t chip;
  esp_chip_info(&chip);
  status->chip_model = chip.model;
  status->chip_revision = chip.revision;
  if (ESP_OK !=
      esp_flash_get_physical_size(NULL, &status->detected_flash_size)) {
    status->detected_flash_size = 0U;
  }

  const esp_partition_t* running = esp_ota_get_running_partition();
  if (!running) {
    status->main_role_check = specter_mock_check_fail;
    status->image_check = specter_mock_check_not_run;
    status->approval_state = specter_mock_approval_read_error;
    status->approval_check = specter_mock_check_not_run;
    return false;
  }
  status->partition_label = running->label;
  status->partition_type = running->type;
  status->partition_subtype = running->subtype;
  status->partition_address = running->address;
  status->partition_size = running->size;
  status->trailer_size = SPECTER_ESP32P4_TRAILER_SIZE;
  status->payload_capacity = running->size - status->trailer_size;
  status->trailer_address = running->address + status->payload_capacity;
  status->main_role_check =
      0 == strcmp(running->label, "main") &&
              running->type == ESP_PARTITION_TYPE_APP &&
              running->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1 &&
              running->address == SPECTER_MAIN_OFFSET &&
              running->size == SPECTER_MAIN_PARTITION_SIZE
          ? specter_mock_check_pass
          : specter_mock_check_fail;

  esp_partition_pos_t position = {
      .offset = running->address,
      .size = status->payload_capacity,
  };
  esp_image_metadata_t metadata = {0};
  if (ESP_OK ==
          esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &position, &metadata) &&
      metadata.image.hash_appended &&
      metadata.image_len <= status->payload_capacity) {
    status->image_check = specter_mock_check_pass;
    status->image_length = metadata.image_len;
    memcpy(status->image_digest, metadata.image_digest,
           sizeof(status->image_digest));
    status->image_digest_present = true;
    status->remaining_bytes = status->payload_capacity - status->image_length;
  } else {
    status->image_check = specter_mock_check_fail;
  }

  size_t trailer_offset = running->size - SPECTER_ESP32P4_TRAILER_SIZE;
  if (ESP_OK != esp_partition_read(running, trailer_offset, &status->approval,
                                   sizeof(status->approval))) {
    status->approval_state = specter_mock_approval_read_error;
    status->approval_check = specter_mock_check_fail;
  } else if (erased_record(&status->approval)) {
    status->approval_state = specter_mock_approval_absent;
    status->approval_check = specter_mock_check_unavailable;
  } else if (!valid_record(&status->approval, running)) {
    status->approval_state = specter_mock_approval_present;
    status->approval_check = specter_mock_check_fail;
  } else {
    status->approval_state = specter_mock_approval_valid;
    status->approval_check = specter_mock_check_pass;
  }
  if (status->approval_state == specter_mock_approval_valid &&
      status->image_digest_present) {
    status->approval_digest_check =
        status->approval.image_length == status->image_length &&
                0 == memcmp(status->approval.image_sha256, status->image_digest,
                            sizeof(status->image_digest))
            ? specter_mock_check_pass
            : specter_mock_check_fail;
  } else {
    status->approval_digest_check = specter_mock_check_unavailable;
  }
  return status->main_role_check == specter_mock_check_pass &&
         status->image_check == specter_mock_check_pass &&
         status->bloat_check == specter_mock_check_pass;
}
