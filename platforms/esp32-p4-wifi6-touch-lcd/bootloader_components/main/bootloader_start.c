/*
 * SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
 * SPDX-FileCopyrightText: 2026 Specter contributors
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file bootloader_start.c
 * @brief Phase 2 fixed-partition Root Loader validation override.
 *
 * This is intentionally a small delta from ESP-IDF v5.5.5's
 * components/bootloader/subproject/main/bootloader_start.c. It retains stock
 * initialization and handoff while replacing OTA selection with an exact
 * compiled partition allow-list. Approval records and trial/fallback state are
 * deliberately outside this Phase 2 validation implementation.
 */

#include <stdbool.h>
#include <inttypes.h>
#include <string.h>
#include <sys/reent.h>

#include "bootloader_common.h"
#include "bootloader_flash_priv.h"
#include "bootloader_hooks.h"
#include "bootloader_init.h"
#include "bootloader_utility.h"
#include "esp_flash_partitions.h"
#include "esp_log.h"
#include "sdkconfig.h"

#define SPECTER_ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

static const char *TAG = "specter-root";

typedef struct {
  const char *label;
  uint8_t type;
  uint8_t subtype;
  uint32_t offset;
  uint32_t size;
  int boot_index;
} specter_root_partition_t;

/* Offsets and partition sizes come from partition_layout.cmake. App sizes
 * include one final 4 KiB sector reserved for the later approval/trial trailer
 * implementation. */
static const specter_root_partition_t boot_allowlist[] = {
  {"boot_a", PART_TYPE_APP, PART_SUBTYPE_FACTORY, SPECTER_BOOT_A_OFFSET,
   SPECTER_BOOTLOADER_PARTITION_SIZE,
   FACTORY_INDEX},
  {"boot_b", PART_TYPE_APP, PART_SUBTYPE_OTA_FLAG | 0, SPECTER_BOOT_B_OFFSET,
   SPECTER_BOOTLOADER_PARTITION_SIZE,
   0},
  {"main", PART_TYPE_APP, PART_SUBTYPE_OTA_FLAG | 1, SPECTER_MAIN_OFFSET,
   SPECTER_MAIN_PARTITION_SIZE,
   1},
};

static bool label_equal(const uint8_t actual[16], const char *expected)
{
  return strncmp((const char *)actual, expected, 16) == 0;
}

static bool entry_equal(const esp_partition_info_t *actual,
                        const specter_root_partition_t *expected)
{
  return label_equal(actual->label, expected->label) &&
         actual->type == expected->type &&
         actual->subtype == expected->subtype &&
         actual->pos.offset == expected->offset &&
         actual->pos.size == expected->size;
}

static const specter_root_partition_t *configured_target(void)
{
#if CONFIG_SPECTER_ROOT_VALIDATION_TARGET_BOOT_A
  return &boot_allowlist[0];
#elif CONFIG_SPECTER_ROOT_VALIDATION_TARGET_BOOT_B
  return &boot_allowlist[1];
#elif CONFIG_SPECTER_ROOT_VALIDATION_TARGET_MAIN
  return &boot_allowlist[2];
#else
#error "A Phase 2 Root Loader validation target must be selected"
#endif
}

static bool load_and_check_layout(esp_partition_pos_t *selected)
{
  const esp_partition_info_t *table =
      bootloader_mmap(ESP_PARTITION_TABLE_OFFSET, ESP_PARTITION_TABLE_MAX_LEN);
  const specter_root_partition_t *target = configured_target();
  bool found[SPECTER_ARRAY_SIZE(boot_allowlist)] = {false};
  int num_partitions = 0;
  bool valid = true;

  if (table == NULL) {
    ESP_LOGE(TAG, "cannot map partition table at 0x%x",
             ESP_PARTITION_TABLE_OFFSET);
    return false;
  }
  if (esp_partition_table_verify(table, true, &num_partitions) != ESP_OK) {
    ESP_LOGE(TAG, "partition table verification failed");
    bootloader_munmap(table);
    return false;
  }

  for (int i = 0; i < num_partitions; i++) {
    const esp_partition_info_t *entry = &table[i];
    bool known_app = false;

    if (entry->type == PART_TYPE_DATA &&
        entry->subtype == PART_SUBTYPE_DATA_OTA) {
      ESP_LOGE(TAG, "otadata is forbidden by the fixed-role boot policy");
      valid = false;
    }

    for (size_t j = 0; j < SPECTER_ARRAY_SIZE(boot_allowlist); j++) {
      const specter_root_partition_t *expected = &boot_allowlist[j];

      if (label_equal(entry->label, expected->label)) {
        if (found[j] || !entry_equal(entry, expected)) {
          ESP_LOGE(TAG, "partition %s does not match the compiled layout",
                   expected->label);
          valid = false;
        } else {
          found[j] = true;
          if (expected == target) {
            *selected = entry->pos;
          }
        }
      }
      if (entry_equal(entry, expected)) {
        known_app = true;
      }
    }

    if (entry->type == PART_TYPE_APP && !known_app) {
      ESP_LOGE(TAG, "unexpected app partition %.16s", entry->label);
      valid = false;
    }
  }

  for (size_t i = 0; i < SPECTER_ARRAY_SIZE(found); i++) {
    if (!found[i]) {
      ESP_LOGE(TAG, "required partition %s is missing", boot_allowlist[i].label);
      valid = false;
    }
  }

  bootloader_munmap(table);
  return valid;
}

/* The stock handoff routine scans its bootloader_state_t on failure. Populate
 * only the selected exact position, leaving every fallback position empty.
 * This preserves stock ESP32-P4 cache/MMU setup without making another image
 * reachable from the Phase 2 validation loader. */
static void __attribute__((noreturn)) load_exact_partition(
    const specter_root_partition_t *target, esp_partition_pos_t selected)
{
  bootloader_state_t isolated = {0};

  if (target->boot_index == FACTORY_INDEX) {
    isolated.factory = selected;
  } else {
    isolated.ota[target->boot_index] = selected;
    isolated.app_count = target->boot_index + 1;
  }

  ESP_LOGI(TAG, "exact-load %s at 0x%" PRIx32 " size 0x%" PRIx32,
           target->label, selected.offset, selected.size);
  bootloader_utility_load_boot_image(&isolated, target->boot_index);
}

void __attribute__((noreturn)) call_start_cpu0(void)
{
  esp_partition_pos_t selected = {0};

  if (bootloader_before_init) {
    bootloader_before_init();
  }
  if (bootloader_init() != ESP_OK) {
    bootloader_reset();
  }
  if (bootloader_after_init) {
    bootloader_after_init();
  }

  /* Validation skipping on deep-sleep wake is intentionally absent. */
  if (!load_and_check_layout(&selected)) {
    bootloader_reset();
  }
  load_exact_partition(configured_target(), selected);
}

#if CONFIG_LIBC_NEWLIB
struct _reent *__getreent(void)
{
  return _GLOBAL_REENT;
}
#endif
