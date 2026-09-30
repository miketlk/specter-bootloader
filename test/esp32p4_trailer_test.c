/**
 * @file esp32p4_trailer_test.c
 * @brief Compile the actual ESP trailer implementation beside legacy ICR tests.
 */

#define SPECTER_JOURNAL_PARTITION_OFFSET 0x620000U
#define SPECTER_JOURNAL_PARTITION_SIZE 0x2000U

// Keep the legacy host flash/ICR tests independent of the ESP implementation.
#define bl_icr_create esp_test_icr_create
#define bl_icr_verify esp_test_icr_verify
#define bl_icr_get_version esp_test_icr_get_version
#define bl_icr_check_sect_size esp_test_icr_check_sect_size
#define bl_vcr_create esp_test_vcr_create
#define bl_vcr_get_version esp_test_vcr_get_version
#include "../platforms/esp32-p4-wifi6-touch-lcd/common/esp32p4_trailer.c"

#include "../platforms/esp32-p4-wifi6-touch-lcd/bootloader_components/main/esp32p4_root_journal.c"

static uint32_t saved_floor;
static uint32_t saved_sequence;
static specter_approval_record_t saved_approvals[4];
static esp_err_t storage_error;
static unsigned storage_writes;
static unsigned image_verifications;
uint8_t esp_test_journal_data[SPECTER_JOURNAL_PARTITION_SIZE];
int esp_test_journal_write_budget;
bool esp_test_fail_approval_erase;
bool esp_test_approval_erased;
unsigned esp_test_approval_erase_attempts;
unsigned esp_test_journal_erase_attempts;
static esp_partition_t journal_partition = {
    .address = SPECTER_JOURNAL_PARTITION_OFFSET,
    .size = SPECTER_JOURNAL_PARTITION_SIZE,
    .label = SPECTER_JOURNAL_PARTITION_LABEL,
};

void esp_test_journal_encrypted(bool encrypted) {
  journal_partition.encrypted = encrypted;
}

const esp_partition_t* esp_partition_find_first(esp_partition_type_t type,
                                                esp_partition_subtype_t subtype,
                                                const char* label) {
  return type == ESP_PARTITION_TYPE_DATA &&
                 subtype == SPECTER_JOURNAL_PARTITION_SUBTYPE &&
                 strcmp(label, SPECTER_JOURNAL_PARTITION_LABEL) == 0
             ? &journal_partition
             : NULL;
}

esp_err_t esp_partition_read_raw(const esp_partition_t* partition,
                                 size_t offset, void* data, size_t size) {
  if (partition != &journal_partition || offset > partition->size ||
      size > partition->size - offset) {
    return ESP_ERR_INVALID_STATE;
  }
  memcpy(data, esp_test_journal_data + offset, size);
  return ESP_OK;
}

esp_err_t esp_partition_write_raw(const esp_partition_t* partition,
                                  size_t offset, const void* data,
                                  size_t size) {
  if (partition != &journal_partition || partition->encrypted ||
      offset > partition->size || size > partition->size - offset ||
      esp_test_journal_write_budget == 0) {
    return ESP_ERR_INVALID_STATE;
  }
  for (size_t i = offset; i < offset + size; ++i) {
    if (esp_test_journal_data[i] != 0xffU) {
      return ESP_ERR_INVALID_STATE;
    }
  }
  if (esp_test_journal_write_budget > 0) {
    --esp_test_journal_write_budget;
  }
  memcpy(esp_test_journal_data + offset, data, size);
  return ESP_OK;
}

void esp_test_storage_reset(void) {
  memset(esp_test_journal_data, 0xff, sizeof(esp_test_journal_data));
  journal_partition.encrypted = false;
  esp_test_journal_write_budget = -1;
  esp_test_fail_approval_erase = false;
  esp_test_approval_erased = false;
  esp_test_approval_erase_attempts = esp_test_journal_erase_attempts = 0U;
  saved_floor = 0U;
  saved_sequence = 0U;
  memset(saved_approvals, 0xff, sizeof(saved_approvals));
  storage_error = ESP_OK;
  storage_writes = image_verifications = 0U;
  memset(pending_sequence, 0, sizeof(pending_sequence));
  pending_sequence[specter_role_main] = 1U;
}

esp_err_t bootloader_flash_read(size_t address, void* data, size_t size,
                                bool allow_decrypt) {
  if (allow_decrypt || address < journal_partition.address) {
    return ESP_ERR_INVALID_STATE;
  }
  return esp_partition_read_raw(
      &journal_partition, address - journal_partition.address, data, size);
}

esp_err_t bootloader_flash_write(size_t address, void* data, size_t size,
                                 bool write_encrypted) {
  if (write_encrypted || address < journal_partition.address) {
    return ESP_ERR_INVALID_STATE;
  }
  return esp_partition_write_raw(
      &journal_partition, address - journal_partition.address, data, size);
}

void esp_test_storage_fail(bool fail) {
  storage_error = fail ? ESP_ERR_INVALID_STATE : ESP_OK;
}

unsigned esp_test_storage_writes(void) { return storage_writes; }
unsigned esp_test_image_verifications(void) { return image_verifications; }

void esp_test_sequence_counter(uint32_t sequence) { saved_sequence = sequence; }

uint32_t esp_test_pending_sequence(specter_esp32p4_role_t role) {
  return pending_sequence[role];
}

void esp_test_sequence_approval(specter_esp32p4_role_t role, uint32_t sequence,
                                bool committed) {
  specter_approval_record_t record = {
      .magic = SPECTER_APPROVAL_MAGIC,
      .revision = SPECTER_APPROVAL_REVISION,
      .record_size = sizeof(record),
      .role = role,
      .semantic_version = 100000099U,
      .image_length = 4096U,
      .sequence = sequence,
      .status = SPECTER_APPROVAL_STATUS_APPROVED,
  };
  strcpy(record.platform, specter_esp32p4_platform_id());
  record.commit_crc =
      record_crc(&record, offsetof(specter_approval_record_t, commit_crc));
  if (!committed) record.commit_crc ^= 1U;
  saved_approvals[role] = record;
}

esp_err_t nvs_open(const char* name, int mode, nvs_handle_t* handle) {
  (void)name;
  (void)mode;
  *handle = 1U;
  return storage_error;
}

esp_err_t nvs_get_u32(nvs_handle_t handle, const char* key, uint32_t* value) {
  (void)handle;
  *value = strcmp(key, "approval_seq") == 0 ? saved_sequence : saved_floor;
  return *value ? ESP_OK : ESP_ERR_NVS_NOT_FOUND;
}

esp_err_t nvs_set_u32(nvs_handle_t handle, const char* key, uint32_t value) {
  (void)handle;
  ++storage_writes;
  if (strcmp(key, "approval_seq") == 0) {
    saved_sequence = value;
  } else {
    saved_floor = value;
  }
  return ESP_OK;
}

esp_err_t nvs_commit(nvs_handle_t handle) {
  (void)handle;
  return ESP_OK;
}

void nvs_close(nvs_handle_t handle) { (void)handle; }

specter_esp32p4_role_t specter_esp32p4_role_for_base(uintptr_t address) {
  if (address == 0x20000U) return specter_role_boot_a;
  if (address == 0x120000U) return specter_role_boot_b;
  return address == 0x220000U ? specter_role_main : specter_role_invalid;
}

const esp_partition_t* specter_esp32p4_partition(specter_esp32p4_role_t role) {
  static const esp_partition_t main = {
      .address = 0x220000U, .size = 0x400000U, .label = "main"};
  static const esp_partition_t boot_a = {.address = 0x20000U,
                                         .size = 0x100000U,
                                         .label = "boot_a",
                                         .encrypted = true};
  static const esp_partition_t boot_b = {.address = 0x120000U,
                                         .size = 0x100000U,
                                         .label = "boot_b",
                                         .encrypted = true};
  if (role == specter_role_boot_a) return &boot_a;
  if (role == specter_role_boot_b) return &boot_b;
  return role == specter_role_main ? &main : NULL;
}

const esp_partition_t* esp_ota_get_running_partition(void) {
  static const esp_partition_t boot = {
      .address = 0x20000U, .size = 0x100000U, .label = "boot_a"};
  return &boot;
}

const char* specter_esp32p4_platform_id(void) {
  return "esp32-p4-wifi6-touch-lcd-4p3";
}

bool specter_esp32p4_candidate_prepared(specter_esp32p4_role_t role) {
  return role == specter_role_main;
}

void specter_esp32p4_candidate_committed(specter_esp32p4_role_t role) {
  (void)role;
}

esp_err_t esp_image_verify(int mode, const esp_partition_pos_t* position,
                           esp_image_metadata_t* metadata) {
  (void)mode;
  (void)position;
  (void)metadata;
  ++image_verifications;
  return ESP_ERR_INVALID_STATE;
}

esp_err_t esp_partition_read(const esp_partition_t* partition, size_t offset,
                             void* data, size_t size) {
  (void)offset;
  if (partition == &journal_partition && !partition->encrypted) {
    return esp_partition_read_raw(partition, offset, data, size);
  }
  specter_esp32p4_role_t role =
      specter_esp32p4_role_for_base(partition->address);
  if (role != specter_role_invalid && offset == trailer_offset(partition) &&
      size == sizeof(saved_approvals[role])) {
    memcpy(data, &saved_approvals[role], size);
    return ESP_OK;
  }
  memset(data, 0xff, size);
  return ESP_OK;
}

esp_err_t esp_partition_write(const esp_partition_t* partition, size_t offset,
                              const void* data, size_t size) {
  (void)partition;
  (void)offset;
  (void)data;
  (void)size;
  ++storage_writes;
  return ESP_OK;
}

esp_err_t esp_partition_erase_range(const esp_partition_t* partition,
                                    size_t offset, size_t size) {
  if (partition == &journal_partition) {
    ++esp_test_journal_erase_attempts;
    if (!esp_test_approval_erased || offset > partition->size ||
        size > partition->size - offset ||
        size != SPECTER_JOURNAL_SECTOR_SIZE) {
      return ESP_ERR_INVALID_STATE;
    }
    memset(esp_test_journal_data + offset, 0xff, size);
  } else {
    ++esp_test_approval_erase_attempts;
    if (esp_test_fail_approval_erase) return ESP_ERR_INVALID_STATE;
    esp_test_approval_erased = true;
  }
  ++storage_writes;
  return ESP_OK;
}
