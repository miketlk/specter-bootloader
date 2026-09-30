/**
 * @file phase2_main.c
 * @brief ESP32-P4 toolchain and Root Loader validation application.
 */

#include <inttypes.h>

#include "esp_app_desc.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_psram.h"

static const char *TAG = "specter-phase2";

#define SPECTER_MIN_FLASH_SIZE_BYTES (16U * 1024U * 1024U)

void app_main(void)
{
  const esp_app_desc_t *app = esp_app_get_description();
  const esp_partition_t *running = esp_ota_get_running_partition();
  uint32_t configured_flash_size = 0;
  uint32_t physical_flash_size = 0;

  ESP_ERROR_CHECK(esp_flash_get_size(NULL, &configured_flash_size));
  ESP_ERROR_CHECK(esp_flash_get_physical_size(NULL, &physical_flash_size));
  if (physical_flash_size < SPECTER_MIN_FLASH_SIZE_BYTES) {
    ESP_LOGE(TAG, "physical flash is smaller than the required 16 MiB");
    ESP_ERROR_CHECK(ESP_ERR_INVALID_SIZE);
  }

  ESP_LOGI(TAG, "SPECTER_PHASE2_OK target=%s", running->label);
  ESP_LOGI(TAG, "IDF=%s app=%s version=%s", app->idf_ver,
           app->project_name, app->version);
  ESP_LOGI(TAG, "flash_configured=%" PRIu32
           " bytes flash_physical=%" PRIu32 " bytes psram=%zu bytes",
           configured_flash_size, physical_flash_size, esp_psram_get_size());
}
