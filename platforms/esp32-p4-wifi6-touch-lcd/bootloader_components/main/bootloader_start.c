/*
 * SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
 * SPDX-FileCopyrightText: 2026 Specter contributors
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file bootloader_start.c
 * @brief Fixed-role Specter Root Loader selection and trial state.
 */

#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <sys/reent.h>

#include "bootloader_common.h"
#include "bootloader_flash_priv.h"
#include "bootloader_hooks.h"
#include "bootloader_init.h"
#include "bootloader_utility.h"
#include "crc32.h"
#include "esp32p4_boot_contract.h"
#include "esp32p4_root_journal.h"
#include "esp_flash_partitions.h"
#include "esp_image_format.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "sdkconfig.h"

#define SPECTER_ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

static const char* TAG = "specter-root";

typedef struct {
  const char* label;
  uint8_t type;
  uint8_t subtype;
  uint32_t offset;
  uint32_t size;
  int boot_index;
  specter_esp32p4_role_t role;
} specter_root_partition_t;

typedef struct {
  bool approved;
  specter_approval_record_t approval;
  specter_boot_journal_state_t journal;
} specter_root_state_t;

static const specter_root_partition_t boot_allowlist[] = {
    {"boot_a", PART_TYPE_APP, PART_SUBTYPE_FACTORY, SPECTER_BOOT_A_OFFSET,
     SPECTER_BOOTLOADER_PARTITION_SIZE, FACTORY_INDEX, specter_role_boot_a},
    {"boot_b", PART_TYPE_APP, PART_SUBTYPE_OTA_FLAG | 0, SPECTER_BOOT_B_OFFSET,
     SPECTER_BOOTLOADER_PARTITION_SIZE, 0, specter_role_boot_b},
    {"main", PART_TYPE_APP, PART_SUBTYPE_OTA_FLAG | 1, SPECTER_MAIN_OFFSET,
     SPECTER_MAIN_PARTITION_SIZE, 1, specter_role_main},
};

static const char* platform_id(void) {
#if SPECTER_ROOT_BOARD_4P3
  return "esp32-p4-wifi6-touch-lcd-4p3";
#elif SPECTER_ROOT_BOARD_5
  return "esp32-p4-wifi6-touch-lcd-5";
#else
#error "A supported ESP32-P4 board must be selected"
#endif
}

static bool label_equal(const uint8_t actual[16], const char* expected) {
  return strncmp((const char*)actual, expected, 16) == 0;
}

static bool entry_equal(const esp_partition_info_t* actual,
                        const specter_root_partition_t* expected) {
  return label_equal(actual->label, expected->label) &&
         actual->type == expected->type &&
         actual->subtype == expected->subtype &&
         actual->pos.offset == expected->offset &&
         actual->pos.size == expected->size;
}

static bool load_and_check_layout(esp_partition_pos_t positions[3]) {
  const esp_partition_info_t* table =
      bootloader_mmap(ESP_PARTITION_TABLE_OFFSET, ESP_PARTITION_TABLE_MAX_LEN);
  bool found[SPECTER_ARRAY_SIZE(boot_allowlist)] = {false};
  int num_partitions = 0;
  bool valid = true;
  bool journal_found = false;

  if (!table ||
      esp_partition_table_verify(table, true, &num_partitions) != ESP_OK) {
    ESP_LOGE(TAG, "partition table verification failed");
    if (table) {
      bootloader_munmap(table);
    }
    return false;
  }

  for (int i = 0; i < num_partitions; ++i) {
    const esp_partition_info_t* entry = &table[i];
    bool known_app = false;
    if (label_equal(entry->label, SPECTER_JOURNAL_PARTITION_LABEL)) {
      if (journal_found || entry->type != PART_TYPE_DATA ||
          entry->subtype != SPECTER_JOURNAL_PARTITION_SUBTYPE ||
          entry->pos.offset != SPECTER_JOURNAL_PARTITION_OFFSET ||
          entry->pos.size != SPECTER_JOURNAL_PARTITION_SIZE ||
          (entry->flags & PART_FLAG_ENCRYPTED)) {
        valid = false;
      }
      journal_found = true;
    }
    if (entry->type == PART_TYPE_DATA &&
        entry->subtype == PART_SUBTYPE_DATA_OTA) {
      ESP_LOGE(TAG, "otadata is forbidden by the fixed-role boot policy");
      valid = false;
    }
    for (size_t j = 0; j < SPECTER_ARRAY_SIZE(boot_allowlist); ++j) {
      if (label_equal(entry->label, boot_allowlist[j].label)) {
        if (found[j] || !entry_equal(entry, &boot_allowlist[j])) {
          ESP_LOGE(TAG, "partition %s does not match compiled layout",
                   boot_allowlist[j].label);
          valid = false;
        } else {
          found[j] = true;
          positions[j] = entry->pos;
        }
      }
      known_app = known_app || entry_equal(entry, &boot_allowlist[j]);
    }
    if (entry->type == PART_TYPE_APP && !known_app) {
      ESP_LOGE(TAG, "unexpected app partition %.16s", entry->label);
      valid = false;
    }
  }
  for (size_t i = 0; i < SPECTER_ARRAY_SIZE(found); ++i) {
    if (!found[i]) {
      ESP_LOGE(TAG, "required partition %s is missing",
               boot_allowlist[i].label);
      valid = false;
    }
  }
  bootloader_munmap(table);
  return valid && journal_found;
}

static bool read_approval(const specter_root_partition_t* partition,
                          const esp_partition_pos_t* position,
                          specter_approval_record_t* approval) {
  const uint32_t offset =
      partition->offset + partition->size - SPECTER_ESP32P4_TRAILER_SIZE;
  esp_image_metadata_t metadata = {0};
  esp_partition_pos_t payload = {
      .offset = partition->offset,
      .size = partition->size - SPECTER_ESP32P4_TRAILER_SIZE,
  };
  if (bootloader_flash_read(offset, approval, sizeof(*approval), true) !=
          ESP_OK ||
      memcmp(approval->magic, SPECTER_APPROVAL_MAGIC,
             sizeof(approval->magic)) != 0 ||
      approval->revision != SPECTER_APPROVAL_REVISION ||
      approval->record_size != sizeof(*approval) ||
      memchr(approval->platform, '\0', sizeof(approval->platform)) == NULL ||
      strcmp(approval->platform, platform_id()) != 0 ||
      approval->role != (uint32_t)partition->role ||
      approval->semantic_version == 0U || approval->image_length == 0U ||
      approval->image_length > payload.size || approval->sequence == 0U ||
      approval->status != SPECTER_APPROVAL_STATUS_APPROVED ||
      approval->commit_crc !=
          crc32_fast(approval, offsetof(specter_approval_record_t, commit_crc),
                     0U) ||
      esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &payload, &metadata) !=
          ESP_OK ||
      !metadata.image.hash_appended ||
      metadata.image_len != approval->image_length ||
      memcmp(metadata.image_digest, approval->image_sha256,
             sizeof(approval->image_sha256)) != 0) {
    return false;
  }
  (void)position;
  return true;
}

static bool consume_request(specter_rtc_request_t* request) {
  rtc_retain_mem_t* retained = bootloader_common_get_rtc_retain_mem();
  if (!retained) {
    return false;
  }
  memcpy(request, retained->custom, sizeof(*request));
  memset(retained->custom, 0, sizeof(retained->custom));
  return esp_rom_get_reset_reason(0) == RESET_REASON_CPU0_SW &&
         request->magic == SPECTER_RTC_REQUEST_MAGIC &&
         request->revision == SPECTER_RTC_REQUEST_REVISION &&
         request->crc ==
             crc32_fast(request, offsetof(specter_rtc_request_t, crc), 0U);
}

static int choose_bootloader(const specter_root_state_t state[3],
                             bool allow_trial) {
  int confirmed = -1;
  for (int i = 0; i < 2; ++i) {
    if (state[i].approved && state[i].journal == specter_journal_confirmed &&
        (confirmed < 0 ||
         state[i].approval.semantic_version >
             state[confirmed].approval.semantic_version ||
         (state[i].approval.semantic_version ==
              state[confirmed].approval.semantic_version &&
          state[i].approval.sequence > state[confirmed].approval.sequence))) {
      confirmed = i;
    }
  }

  if (!allow_trial) {
    return confirmed;
  }

  int trial = -1;
  for (int i = 0; i < 2; ++i) {
    if (state[i].approved && state[i].journal == specter_journal_none &&
        (confirmed < 0 || state[i].approval.semantic_version >
                              state[confirmed].approval.semantic_version) &&
        (trial < 0 ||
         state[i].approval.semantic_version >
             state[trial].approval.semantic_version ||
         (state[i].approval.semantic_version ==
              state[trial].approval.semantic_version &&
          state[i].approval.sequence > state[trial].approval.sequence))) {
      trial = i;
    }
  }
  return trial >= 0 ? trial : confirmed;
}

static int prepare_bootloader(const specter_root_state_t state[3]) {
  int selected = choose_bootloader(state, true);
  if (selected < 0) {
    ESP_LOGE(TAG, "no approved healthy Specter Bootloader");
    return -1;
  }
  if (state[selected].journal == specter_journal_none) {
    if (!specter_root_journal_append(boot_allowlist[selected].role,
                                     state[selected].approval.sequence,
                                     specter_journal_attempted)) {
      ESP_LOGE(TAG, "cannot mark bootloader trial attempted");
      // The append may have partially or fully committed despite an I/O error.
      // Exclude all trials for this boot; the next boot rescans durable
      // records.
      selected = choose_bootloader(state, false);
      if (selected >= 0) {
        ESP_LOGI(TAG, "fallback %s", boot_allowlist[selected].label);
      }
      return selected;
    }
    ESP_LOGI(TAG, "trial %s sequence %" PRIu32, boot_allowlist[selected].label,
             state[selected].approval.sequence);
  }
  return selected;
}

static void __attribute__((noreturn)) load_exact_partition(
    const specter_root_partition_t* target, esp_partition_pos_t selected) {
  bootloader_state_t isolated = {0};
  if (target->boot_index == FACTORY_INDEX) {
    isolated.factory = selected;
  } else {
    isolated.ota[target->boot_index] = selected;
    isolated.app_count = target->boot_index + 1;
  }
  ESP_LOGI(TAG, "exact-load %s at 0x%" PRIx32 " size 0x%" PRIx32, target->label,
           selected.offset, selected.size);
  bootloader_utility_load_boot_image(&isolated, target->boot_index);
}

void __attribute__((noreturn)) call_start_cpu0(void) {
  esp_partition_pos_t positions[3] = {0};
  specter_root_state_t state[3] = {0};
  specter_rtc_request_t request = {0};

  if (bootloader_before_init) {
    bootloader_before_init();
  }
  if (bootloader_init() != ESP_OK) {
    bootloader_reset();
  }
  if (bootloader_after_init) {
    bootloader_after_init();
  }
  if (!load_and_check_layout(positions)) {
    bootloader_reset();
  }

  for (size_t i = 0; i < SPECTER_ARRAY_SIZE(boot_allowlist); ++i) {
    state[i].approved =
        read_approval(&boot_allowlist[i], &positions[i], &state[i].approval);
    if (state[i].approved && i < 2U) {
      state[i].journal = specter_root_journal_state(boot_allowlist[i].role,
                                                    state[i].approval.sequence);
    }
  }

  bool valid_request = consume_request(&request);
  if (valid_request && request.command == specter_rtc_confirm_bootloader &&
      request.target >= specter_role_boot_a &&
      request.target <= specter_role_boot_b) {
    int index = (int)request.target - (int)specter_role_boot_a;
    if (state[index].approved &&
        state[index].approval.sequence == request.sequence &&
        (state[index].journal == specter_journal_confirmed ||
         (state[index].journal == specter_journal_attempted &&
          specter_root_journal_append(boot_allowlist[index].role,
                                      request.sequence,
                                      specter_journal_confirmed)))) {
      state[index].journal = specter_journal_confirmed;
      ESP_LOGI(TAG, "confirmed %s sequence %" PRIu32,
               boot_allowlist[index].label, request.sequence);
    } else {
      ESP_LOGE(TAG, "invalid bootloader confirmation request");
    }
  }

  if (valid_request && request.command == specter_rtc_boot_main &&
      request.target == specter_role_main && state[2].approved &&
      state[2].approval.sequence == request.sequence) {
    load_exact_partition(&boot_allowlist[2], positions[2]);
  }

  int selected = prepare_bootloader(state);
  if (selected < 0) {
    bootloader_reset();
  }
  load_exact_partition(&boot_allowlist[selected], positions[selected]);
}

#if CONFIG_LIBC_NEWLIB
struct _reent* __getreent(void) { return _GLOBAL_REENT; }
#endif
