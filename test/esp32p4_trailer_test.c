/**
 * @file esp32p4_trailer_test.c
 * @brief Compile the actual ESP trailer implementation beside legacy ICR tests.
 */

// Keep the legacy host flash/ICR tests independent of the ESP implementation.
#define bl_icr_create esp_test_icr_create
#define bl_icr_verify esp_test_icr_verify
#define bl_icr_get_version esp_test_icr_get_version
#define bl_icr_check_sect_size esp_test_icr_check_sect_size
#define bl_vcr_create esp_test_vcr_create
#define bl_vcr_get_version esp_test_vcr_get_version
#include "../platforms/esp32-p4-wifi6-touch-lcd/common/esp32p4_trailer.c"

static uint32_t saved_floor;
static esp_err_t storage_error;
static unsigned storage_writes;
static unsigned image_verifications;

void esp_test_storage_reset(void) {
  saved_floor = 0U;
  storage_error = ESP_OK;
  storage_writes = image_verifications = 0U;
  pending_sequence[specter_role_main] = 1U;
}

void esp_test_storage_fail(bool fail) {
  storage_error = fail ? ESP_ERR_INVALID_STATE : ESP_OK;
}

unsigned esp_test_storage_writes(void) { return storage_writes; }
unsigned esp_test_image_verifications(void) { return image_verifications; }

esp_err_t nvs_open(const char* name, int mode, nvs_handle_t* handle) {
  (void)name;
  (void)mode;
  *handle = 1U;
  return storage_error;
}

esp_err_t nvs_get_u32(nvs_handle_t handle, const char* key, uint32_t* value) {
  (void)handle;
  (void)key;
  *value = saved_floor;
  return saved_floor ? ESP_OK : ESP_ERR_NVS_NOT_FOUND;
}

esp_err_t nvs_set_u32(nvs_handle_t handle, const char* key, uint32_t value) {
  (void)handle;
  (void)key;
  ++storage_writes;
  saved_floor = value;
  return ESP_OK;
}

esp_err_t nvs_commit(nvs_handle_t handle) {
  (void)handle;
  return ESP_OK;
}

void nvs_close(nvs_handle_t handle) { (void)handle; }

specter_esp32p4_role_t specter_esp32p4_role_for_base(uintptr_t address) {
  return address == 0x220000U ? specter_role_main : specter_role_invalid;
}

const esp_partition_t* specter_esp32p4_partition(specter_esp32p4_role_t role) {
  static const esp_partition_t main = {
      .address = 0x220000U, .size = 0x400000U, .label = "main"};
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
  (void)partition;
  (void)offset;
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
  (void)partition;
  (void)offset;
  (void)size;
  ++storage_writes;
  return ESP_OK;
}
