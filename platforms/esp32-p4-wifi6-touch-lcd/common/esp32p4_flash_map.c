/**
 * @file esp32p4_flash_map.c
 * @brief Fixed-role ESP-IDF partition map and guarded flash access.
 */

#include <stdarg.h>
#include <string.h>

#include "bl_syscalls.h"
#include "crc32.h"
#include "esp32p4_platform.h"
#include "esp32p4_trailer.h"
#include "esp_app_desc.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"

static const char* TAG = "specter-flash";

static const specter_esp32p4_fixed_partition_t fixed_layout[] = {
    {"boot_a", ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_FACTORY,
     SPECTER_BOOT_A_OFFSET, SPECTER_BOOTLOADER_PARTITION_SIZE,
     specter_role_boot_a},
    {"boot_b", ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0,
     SPECTER_BOOT_B_OFFSET, SPECTER_BOOTLOADER_PARTITION_SIZE,
     specter_role_boot_b},
    {"main", ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_1,
     SPECTER_MAIN_OFFSET, SPECTER_MAIN_PARTITION_SIZE, specter_role_main},
};

static const esp_partition_t* resolved[4];
static bool candidate_invalidated[4];

const specter_esp32p4_fixed_partition_t* specter_esp32p4_fixed_layout(
    size_t* count) {
  if (count) {
    *count = sizeof(fixed_layout) / sizeof(fixed_layout[0]);
  }
  return fixed_layout;
}

static bool exact_partition_matches(
    const esp_partition_t* actual,
    const specter_esp32p4_fixed_partition_t* expected) {
  return actual && expected && 0 == strcmp(actual->label, expected->label) &&
         actual->type == expected->type &&
         actual->subtype == expected->subtype &&
         actual->address == expected->address && actual->size == expected->size;
}

bool specter_esp32p4_flash_map_init(void) {
  memset(resolved, 0, sizeof(resolved));
  memset(candidate_invalidated, 0, sizeof(candidate_invalidated));

  for (size_t i = 0; i < sizeof(fixed_layout) / sizeof(fixed_layout[0]); ++i) {
    const specter_esp32p4_fixed_partition_t* expected = &fixed_layout[i];
    const esp_partition_t* actual = esp_partition_find_first(
        expected->type, expected->subtype, expected->label);
    if (!exact_partition_matches(actual, expected)) {
      ESP_LOGE(TAG, "fixed partition %s does not match compiled layout",
               expected->label);
      return false;
    }
    resolved[expected->role] = actual;
  }

  esp_partition_iterator_t it = esp_partition_find(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, NULL);
  for (; it; it = esp_partition_next(it)) {
    const esp_partition_t* part = esp_partition_get(it);
    bool expected = false;
    for (size_t i = 0; i < sizeof(fixed_layout) / sizeof(fixed_layout[0]);
         ++i) {
      expected = expected || exact_partition_matches(part, &fixed_layout[i]);
    }
    if (!expected) {
      ESP_LOGE(TAG, "unexpected application partition %s", part->label);
      esp_partition_iterator_release(it);
      return false;
    }
  }
  return specter_esp32p4_journal_partition() != NULL;
}

const esp_partition_t* specter_esp32p4_partition(specter_esp32p4_role_t role) {
  return role > specter_role_invalid && role <= specter_role_main
             ? resolved[role]
             : NULL;
}

const esp_partition_t* specter_esp32p4_partition_for_range(
    uintptr_t address, size_t size, size_t* relative_offset,
    specter_esp32p4_role_t* role) {
  if (size > UINTPTR_MAX - address) {
    return NULL;
  }
  for (int current = specter_role_boot_a; current <= specter_role_main;
       ++current) {
    const esp_partition_t* part = resolved[current];
    if (part && address >= part->address &&
        address + size <= (uintptr_t)part->address + part->size) {
      if (relative_offset) {
        *relative_offset = address - part->address;
      }
      if (role) {
        *role = (specter_esp32p4_role_t)current;
      }
      return part;
    }
  }
  return NULL;
}

specter_esp32p4_role_t specter_esp32p4_role_for_base(uintptr_t address) {
  for (int role = specter_role_boot_a; role <= specter_role_main; ++role) {
    if (resolved[role] && resolved[role]->address == address) {
      return (specter_esp32p4_role_t)role;
    }
  }
  return specter_role_invalid;
}

static bool prepare_candidate(specter_esp32p4_role_t role) {
  if (!candidate_invalidated[role]) {
    if (!specter_esp32p4_approval_invalidate(role)) {
      return false;
    }
    candidate_invalidated[role] = true;
  }
  return true;
}

bool specter_esp32p4_candidate_prepared(specter_esp32p4_role_t role) {
  return role > specter_role_invalid && role <= specter_role_main &&
         candidate_invalidated[role];
}

void specter_esp32p4_candidate_committed(specter_esp32p4_role_t role) {
  if (role > specter_role_invalid && role <= specter_role_main) {
    candidate_invalidated[role] = false;
  }
}

bool blsys_flash_map_get_items(int items, ...) {
  const esp_partition_t* boot_a = resolved[specter_role_boot_a];
  const esp_partition_t* boot_b = resolved[specter_role_boot_b];
  const esp_partition_t* main = resolved[specter_role_main];
  if (items < 0 || !boot_a || !boot_b || !main) {
    return false;
  }

  const bl_addr_t map[bl_flash_map_nitems] = {
      [bl_flash_firmware_base] = main->address,
      [bl_flash_firmware_size] = main->size,
      [bl_flash_firmware_part1_size] = 0x1000U,
      [bl_flash_bootloader_image_base] = boot_a->address,
      [bl_flash_bootloader_copy1_base] = boot_a->address,
      [bl_flash_bootloader_copy2_base] = boot_b->address,
      [bl_flash_bootloader_size] = boot_a->size,
  };

  bool valid = true;
  va_list args;
  va_start(args, items);
  for (int i = 0; i < items; ++i) {
    int item = va_arg(args, int);
    bl_addr_t* output = va_arg(args, bl_addr_t*);
    if (item < 0 || item >= bl_flash_map_nitems || !output) {
      valid = false;
    } else {
      *output = map[item];
    }
  }
  va_end(args);
  return valid;
}

const char* blsys_payload_format(void) { return "esp-idf-app"; }

bool blsys_flash_finalize(bl_addr_t address, uint32_t section_size,
                          uint32_t image_size, uint32_t image_version,
                          const uint8_t* expected_sha256,
                          size_t expected_sha256_size) {
  specter_esp32p4_role_t role = specter_esp32p4_role_for_base(address);
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  return partition && partition->size == section_size && expected_sha256 &&
         expected_sha256_size == 32U &&
         specter_esp32p4_approval_create_authorized(
             role, image_size, image_version, expected_sha256);
}

bool blsys_flash_erase(bl_addr_t address, size_t size) {
  size_t offset = 0;
  specter_esp32p4_role_t role = specter_role_invalid;
  const esp_partition_t* part =
      specter_esp32p4_partition_for_range(address, size, &offset, &role);
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (!part || !size || (offset % SPECTER_ESP32P4_TRAILER_SIZE) ||
      (size % SPECTER_ESP32P4_TRAILER_SIZE) ||
      (running && running->address == part->address) ||
      !prepare_candidate(role)) {
    return false;
  }

  size_t image_limit = part->size - SPECTER_ESP32P4_TRAILER_SIZE;
  if (offset >= image_limit) {
    return true;  // prepare_candidate() already erased the trailer sector.
  }
  size_t erase_size = size;
  if (offset + erase_size > image_limit) {
    erase_size = image_limit - offset;
  }
  return erase_size &&
         ESP_OK == esp_partition_erase_range(part, offset, erase_size);
}

bool blsys_flash_read(bl_addr_t address, void* buffer, size_t length) {
  size_t offset = 0;
  const esp_partition_t* part =
      specter_esp32p4_partition_for_range(address, length, &offset, NULL);
  return buffer && part &&
         ESP_OK == esp_partition_read(part, offset, buffer, length);
}

bool blsys_flash_write(bl_addr_t address, const void* buffer, size_t length) {
  size_t offset = 0;
  specter_esp32p4_role_t role = specter_role_invalid;
  const esp_partition_t* part =
      specter_esp32p4_partition_for_range(address, length, &offset, &role);
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (!part || !buffer || !length ||
      offset + length > part->size - SPECTER_ESP32P4_TRAILER_SIZE ||
      (running && running->address == part->address) ||
      !prepare_candidate(role)) {
    return false;
  }
  return ESP_OK == esp_partition_write(part, offset, buffer, length);
}

bool blsys_flash_crc32(uint32_t* crc, bl_addr_t address, size_t length) {
  uint8_t buffer[1024];
  if (!crc) {
    return false;
  }
  while (length) {
    size_t chunk = length < sizeof(buffer) ? length : sizeof(buffer);
    if (!blsys_flash_read(address, buffer, chunk)) {
      return false;
    }
    *crc = crc32_fast(buffer, chunk, *crc);
    address += chunk;
    length -= chunk;
  }
  return true;
}

bool blsys_flash_write_protect(bl_addr_t address, size_t size, bool enable) {
  size_t offset = 0;
  const esp_partition_t* part =
      specter_esp32p4_partition_for_range(address, size, &offset, NULL);
  (void)enable;
  return part && 0U == offset && size == part->size;
}

bool blsys_flash_read_protect(int level) {
  (void)level;
  return true;
}

int blsys_flash_get_read_protection_level(void) {
#if CONFIG_SECURE_FLASH_ENC_ENABLED
  return 1;
#else
  return 0;
#endif
}
