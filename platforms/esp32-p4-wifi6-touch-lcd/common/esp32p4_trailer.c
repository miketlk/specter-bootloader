/**
 * @file esp32p4_trailer.c
 * @brief Power-loss-safe ESP32-P4 approval and trial records.
 */

#include "esp32p4_trailer.h"

#include <stddef.h>
#include <string.h>

#include "bl_integrity_check.h"
#include "bl_syscalls.h"
#include "bl_util.h"
#include "crc32.h"
#include "esp32p4_platform.h"
#include "esp_image_format.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "nvs.h"

#define SPECTER_JOURNAL_OFFSET 0x80U

static const char* TAG = "specter-approval";
static uint32_t pending_sequence[4];

static bool firmware_version_floor_set(bl_addr_t address,
                                       uint32_t image_version);
static uint32_t firmware_version_floor_get(bl_addr_t address);

_Static_assert(sizeof(specter_approval_record_t) <= SPECTER_JOURNAL_OFFSET,
               "approval record overlaps boot journal");
_Static_assert(SPECTER_JOURNAL_OFFSET + sizeof(specter_boot_journal_record_t) <=
                   SPECTER_ESP32P4_TRAILER_SIZE,
               "boot journal does not fit trailer");
_Static_assert(0U == SPECTER_JOURNAL_OFFSET % SPECTER_FLASH_WRITE_GRANULE,
               "boot journal offset is not encryption-block aligned");
_Static_assert(0U == offsetof(specter_approval_record_t, commit_crc) %
                        SPECTER_FLASH_WRITE_GRANULE,
               "approval commit is not encryption-block aligned");
_Static_assert(SPECTER_FLASH_WRITE_GRANULE ==
                   sizeof(specter_approval_record_t) -
                       offsetof(specter_approval_record_t, commit_crc),
               "approval commit must occupy one encryption block");
_Static_assert(0U == offsetof(specter_boot_journal_record_t, commit_crc) %
                        SPECTER_FLASH_WRITE_GRANULE,
               "journal commit is not encryption-block aligned");
_Static_assert(SPECTER_FLASH_WRITE_GRANULE ==
                   sizeof(specter_boot_journal_record_t) -
                       offsetof(specter_boot_journal_record_t, commit_crc),
               "journal commit must occupy one encryption block");
_Static_assert(0U == sizeof(specter_boot_journal_record_t) %
                        SPECTER_FLASH_WRITE_GRANULE,
               "journal stride is not encryption-block aligned");

static uint32_t record_crc(const void* record, size_t crc_offset) {
  return crc32_fast(record, crc_offset, 0U);
}

static size_t trailer_offset(const esp_partition_t* partition) {
  return partition->size - SPECTER_ESP32P4_TRAILER_SIZE;
}

static bool write_encrypted_granules(const esp_partition_t* partition,
                                     size_t offset, const void* data,
                                     size_t size) {
  return partition && data && size > 0U &&
         0U == offset % SPECTER_FLASH_WRITE_GRANULE &&
         0U == size % SPECTER_FLASH_WRITE_GRANULE &&
         ESP_OK == esp_partition_write(partition, offset, data, size);
}

static const char* floor_key(specter_esp32p4_role_t role) {
  switch (role) {
    case specter_role_boot_a:
    case specter_role_boot_b:
      return "floor_boot";
    case specter_role_main:
      return "floor_main";
    default:
      return NULL;
  }
}

static bool reserve_sequence(specter_esp32p4_role_t role,
                             uint32_t previous_sequence) {
  nvs_handle_t handle;
  if (role <= specter_role_invalid || role > specter_role_main ||
      ESP_OK != nvs_open("specter", NVS_READWRITE, &handle)) {
    return false;
  }
  uint32_t current = 0U;
  esp_err_t result = nvs_get_u32(handle, "approval_seq", &current);
  if (ESP_ERR_NVS_NOT_FOUND == result) {
    result = ESP_OK;
  }
  if (ESP_OK == result && previous_sequence > current) {
    current = previous_sequence;
  }
  if (ESP_OK == result && current == UINT32_MAX) {
    result = ESP_ERR_INVALID_STATE;
  }
  if (ESP_OK == result) {
    ++current;
    result = nvs_set_u32(handle, "approval_seq", current);
  }
  if (ESP_OK == result) {
    result = nvs_commit(handle);
  }
  nvs_close(handle);
  if (ESP_OK != result) {
    return false;
  }
  pending_sequence[role] = current;
  return true;
}

static bool approval_record_valid(const specter_approval_record_t* record,
                                  specter_esp32p4_role_t role,
                                  const esp_partition_t* partition) {
  return record && partition &&
         0 == memcmp(record->magic, SPECTER_APPROVAL_MAGIC,
                     sizeof(record->magic)) &&
         record->revision == SPECTER_APPROVAL_REVISION &&
         record->record_size == sizeof(*record) &&
         0 == strncmp(record->platform, specter_esp32p4_platform_id(),
                      sizeof(record->platform)) &&
         record->role == (uint32_t)role &&
         record->semantic_version > BL_VERSION_NA &&
         record->semantic_version <= BL_VERSION_MAX &&
         record->image_length > 0U &&
         record->image_length <=
             partition->size - SPECTER_ESP32P4_TRAILER_SIZE &&
         record->sequence > 0U &&
         record->status == SPECTER_APPROVAL_STATUS_APPROVED &&
         record->commit_crc ==
             record_crc(record,
                        offsetof(specter_approval_record_t, commit_crc));
}

static bool verify_exact_image(const esp_partition_t* partition,
                               const specter_approval_record_t* record) {
  esp_partition_pos_t position = {
      .offset = partition->address,
      .size = partition->size - SPECTER_ESP32P4_TRAILER_SIZE,
  };
  esp_image_metadata_t metadata = {0};
  if (ESP_OK !=
          esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &position, &metadata) ||
      !metadata.image.hash_appended ||
      metadata.image_len != record->image_length ||
      0 != memcmp(metadata.image_digest, record->image_sha256,
                  sizeof(record->image_sha256))) {
    return false;
  }
  return true;
}

bool specter_esp32p4_approval_read(specter_esp32p4_role_t role,
                                   specter_approval_record_t* record,
                                   bool verify_image) {
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  specter_approval_record_t local;
  if (!record) {
    record = &local;
  }
  if (!partition ||
      ESP_OK != esp_partition_read(partition, trailer_offset(partition), record,
                                   sizeof(*record)) ||
      !approval_record_valid(record, role, partition)) {
    return false;
  }
  return !verify_image || verify_exact_image(partition, record);
}

bool specter_esp32p4_approval_invalidate(specter_esp32p4_role_t role) {
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  const esp_partition_t* running = esp_ota_get_running_partition();
  specter_approval_record_t previous;
  if (!partition || (running && running->address == partition->address)) {
    return false;
  }
  uint32_t previous_sequence = 0U;
  if (specter_esp32p4_approval_read(role, &previous, false)) {
    previous_sequence = previous.sequence;
  }
  if (!reserve_sequence(role, previous_sequence)) {
    return false;
  }
  return ESP_OK == esp_partition_erase_range(partition,
                                             trailer_offset(partition),
                                             SPECTER_ESP32P4_TRAILER_SIZE);
}

bool specter_esp32p4_approval_create(specter_esp32p4_role_t role,
                                     uint32_t image_length,
                                     uint32_t semantic_version) {
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (!partition || !specter_esp32p4_candidate_prepared(role) ||
      !pending_sequence[role] || !image_length ||
      image_length > partition->size - SPECTER_ESP32P4_TRAILER_SIZE ||
      semantic_version <= BL_VERSION_NA || semantic_version > BL_VERSION_MAX ||
      (running && running->address == partition->address)) {
    return false;
  }

  specter_approval_record_t record = {
      .magic = SPECTER_APPROVAL_MAGIC,
      .revision = SPECTER_APPROVAL_REVISION,
      .record_size = sizeof(record),
      .role = role,
      .semantic_version = semantic_version,
      .image_length = image_length,
      .sequence = pending_sequence[role],
      .status = SPECTER_APPROVAL_STATUS_APPROVED,
      .prefix_padding = UINT32_MAX,
      .commit_crc = UINT32_MAX,
  };
  if (strlen(specter_esp32p4_platform_id()) >= sizeof(record.platform)) {
    return false;
  }
  strcpy(record.platform, specter_esp32p4_platform_id());

  esp_partition_pos_t position = {
      .offset = partition->address,
      .size = partition->size - SPECTER_ESP32P4_TRAILER_SIZE,
  };
  esp_image_metadata_t metadata = {0};
  if (ESP_OK != esp_image_verify(ESP_IMAGE_VERIFY, &position, &metadata) ||
      !metadata.image.hash_appended || metadata.image_len != image_length) {
    ESP_LOGE(TAG, "candidate %s is not an exact hash-appended ESP image",
             partition->label);
    return false;
  }
  memcpy(record.image_sha256, metadata.image_digest,
         sizeof(record.image_sha256));

  if (!firmware_version_floor_set(partition->address, semantic_version)) {
    return false;
  }

  const size_t offset = trailer_offset(partition);
  const size_t prefix_size = offsetof(specter_approval_record_t, commit_crc);
  if (!write_encrypted_granules(partition, offset, &record, prefix_size)) {
    return false;
  }
  record.commit_crc = record_crc(&record, prefix_size);
  if (!write_encrypted_granules(partition, offset + prefix_size,
                                (const uint8_t*)&record + prefix_size,
                                sizeof(record) - prefix_size)) {
    return false;
  }
  specter_esp32p4_candidate_committed(role);
  pending_sequence[role] = 0U;

  specter_approval_record_t check;
  return specter_esp32p4_approval_read(role, &check, true);
}

bool specter_esp32p4_journal_append(specter_esp32p4_role_t role,
                                    uint32_t sequence,
                                    specter_boot_journal_state_t state) {
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  if (!partition ||
      (role != specter_role_boot_a && role != specter_role_boot_b) ||
      (state != specter_journal_attempted &&
       state != specter_journal_confirmed)) {
    return false;
  }

  specter_boot_journal_record_t existing;
  size_t record_offset = trailer_offset(partition) + SPECTER_JOURNAL_OFFSET;
  const size_t trailer_end = partition->size;
  for (; record_offset + sizeof(existing) <= trailer_end;
       record_offset += sizeof(existing)) {
    if (ESP_OK != esp_partition_read(partition, record_offset, &existing,
                                     sizeof(existing))) {
      return false;
    }
    uint32_t erased = UINT32_MAX;
    if (0 == memcmp(&existing.magic, &erased, sizeof(erased))) {
      break;
    }
  }
  if (record_offset + sizeof(existing) > trailer_end) {
    return false;
  }

  specter_boot_journal_record_t record = {
      .magic = SPECTER_JOURNAL_MAGIC,
      .revision = SPECTER_JOURNAL_REVISION,
      .sequence = sequence,
      .state = state,
      .commit_crc = UINT32_MAX,
  };
  const size_t prefix_size =
      offsetof(specter_boot_journal_record_t, commit_crc);
  if (!write_encrypted_granules(partition, record_offset, &record,
                                prefix_size)) {
    return false;
  }
  record.commit_crc = record_crc(&record, prefix_size);
  return write_encrypted_granules(partition, record_offset + prefix_size,
                                  (const uint8_t*)&record + prefix_size,
                                  sizeof(record) - prefix_size);
}

bool bl_icr_create(bl_addr_t address, uint32_t section_size,
                   uint32_t image_size, uint32_t image_version) {
  specter_esp32p4_role_t role = specter_esp32p4_role_for_base(address);
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  return partition && partition->size == section_size &&
         specter_esp32p4_approval_create(role, image_size, image_version);
}

bool bl_icr_verify(bl_addr_t address, uint32_t section_size,
                   uint32_t* image_version) {
  specter_esp32p4_role_t role = specter_esp32p4_role_for_base(address);
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  specter_approval_record_t record;
  if (image_version) {
    *image_version = BL_VERSION_NA;
  }
  if (!partition || partition->size != section_size ||
      !specter_esp32p4_approval_read(role, &record, true)) {
    return false;
  }
  if (image_version) {
    *image_version = record.semantic_version;
  }
  return true;
}

bool bl_icr_get_version(bl_addr_t address, uint32_t section_size,
                        uint32_t* image_version) {
  specter_esp32p4_role_t role = specter_esp32p4_role_for_base(address);
  const esp_partition_t* partition = specter_esp32p4_partition(role);
  specter_approval_record_t record;
  if (image_version) {
    *image_version = BL_VERSION_NA;
  }
  if (!partition || partition->size != section_size || !image_version ||
      !specter_esp32p4_approval_read(role, &record, false)) {
    return false;
  }
  *image_version = record.semantic_version;
  return true;
}

bool bl_icr_check_sect_size(uint32_t section_size, uint32_t image_size) {
  return section_size > SPECTER_ESP32P4_TRAILER_SIZE && image_size > 0U &&
         image_size <= section_size - SPECTER_ESP32P4_TRAILER_SIZE;
}

static bool firmware_version_floor_set(bl_addr_t address,
                                       uint32_t image_version) {
  specter_esp32p4_role_t role = specter_esp32p4_role_for_base(address);
  const char* key = floor_key(role);
  if (!key || image_version <= BL_VERSION_NA ||
      image_version > BL_VERSION_MAX) {
    return false;
  }
  nvs_handle_t handle;
  if (ESP_OK != nvs_open("specter", NVS_READWRITE, &handle)) {
    return false;
  }
  uint32_t current = BL_VERSION_NA;
  esp_err_t result = nvs_get_u32(handle, key, &current);
  if (ESP_ERR_NVS_NOT_FOUND == result) {
    result = ESP_OK;
  }
  if (ESP_OK == result && image_version > current) {
    result = nvs_set_u32(handle, key, image_version);
    if (ESP_OK == result) {
      result = nvs_commit(handle);
    }
  }
  nvs_close(handle);
  return ESP_OK == result;
}

static uint32_t firmware_version_floor_get(bl_addr_t address) {
  const char* key = floor_key(specter_esp32p4_role_for_base(address));
  nvs_handle_t handle;
  uint32_t version = BL_VERSION_NA;
  if (!key || ESP_OK != nvs_open("specter", NVS_READONLY, &handle)) {
    return version;
  }
  if (ESP_OK != nvs_get_u32(handle, key, &version)) {
    version = BL_VERSION_NA;
  }
  nvs_close(handle);
  return version;
}

bool bl_vcr_create(bl_addr_t address, uint32_t section_size,
                   uint32_t image_version, bl_vcr_place_t place) {
  (void)section_size;
  return (bl_vcr_starting == place || bl_vcr_ending == place) &&
         firmware_version_floor_set(address, image_version);
}

uint32_t bl_vcr_get_version(bl_addr_t address, uint32_t section_size,
                            bl_vcr_place_t place) {
  (void)section_size;
  return ((int)place & (int)bl_vcr_any)
             ? firmware_version_floor_get(address)
             : BL_VERSION_NA;
}
